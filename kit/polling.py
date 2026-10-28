"""Long polling that confirms an update to Telegram only after handling it.

Telegram keeps an update until a getUpdates call asks for an offset past it.
PTB's Updater sends that offset as soon as the update is queued, before any
handler has run, and on shutdown it confirms whatever is queued before the
queue is drained. A process killed mid-handler (kill -9, out of memory, the
SIGKILL that ends a deploy's grace period) lost the update it was handling and
every update queued behind it.

Here the offset moves past an update only once process_update has returned,
so the next getUpdates is the confirmation. A process that dies mid-handler
leaves the update with Telegram, and the next start handles it again: at least
once, like the webhook (ADR 4, ADR 8). One update per call, because a batch is
confirmed whole: a crash would replay the updates of the batch already handled.
"""

import asyncio
import logging
import signal
import warnings
from collections.abc import Awaitable, Callable
from datetime import timedelta

from telegram import Bot
from telegram.error import Conflict, InvalidToken, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application
from telegram.warnings import PTBDeprecationWarning

log = logging.getLogger(__name__)

POLL_TIMEOUT = 10  # seconds Telegram holds an empty getUpdates open, PTB's default
FIRST_PAUSE = 1.0
MAX_PAUSE = 30.0
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)

Sleep = Callable[[float], Awaitable[None]]


def run(application: Application) -> None:
    """What `python -m kit run` does: polls until SIGINT or SIGTERM.

    A signal lets the running update finish. A second one changes nothing:
    SIGKILL is what ends a hung handler, and its update is then handled again
    on the next start."""
    asyncio.run(_main(application))


async def _main(application: Application) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in STOP_SIGNALS:
        loop.add_signal_handler(sig, stop.set)
    await serve(application, stop)


async def serve(application: Application, stop: asyncio.Event) -> None:
    """The application's life in PTB's run_polling order, so post_init and
    post_shutdown run where they ran there."""
    await application.initialize()
    try:
        if application.post_init is not None:
            await application.post_init(application)
        # getUpdates is refused while a webhook is set; PTB's start-up did the same.
        await application.bot.delete_webhook()
        await poll(application, stop)
    finally:
        await application.shutdown()
        if application.post_shutdown is not None:
            await application.post_shutdown(application)


async def poll(
    application: Application,
    stop: asyncio.Event,
    *,
    timeout: float = POLL_TIMEOUT,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Hands updates to process_update one at a time until `stop` is set.
    An update that is running then finishes first and is confirmed."""
    bot = application.bot
    offset = 0  # 0: the oldest update Telegram still holds
    confirmed = 0  # the offset of the last getUpdates that came back
    pause = 0.0
    while not stop.is_set():
        try:
            updates = await _unless(stop, bot.get_updates(offset=offset, limit=1, timeout=timeout))
        except InvalidToken:
            raise
        except RetryAfter as e:
            pause = _retry_after(e) + 0.5
            log.warning("getUpdates: flood control, retrying in %.1f s", pause)
        except TimedOut:
            pause = 0.0  # the long poll ran out on our side: ask again at once
        except Conflict:
            pause = _longer(pause)
            log.error(
                "getUpdates refused: another process polls with this token or a webhook "
                "is set, retrying in %.1f s",
                pause,
            )
        except TelegramError as e:
            pause = _longer(pause)
            log.warning("getUpdates failed (%s), retrying in %.1f s", type(e).__name__, pause)
        except Exception:
            pause = _longer(pause)
            log.exception("getUpdates answer not processed, retrying in %.1f s", pause)
        else:
            if updates is None:
                break  # stopped while waiting
            confirmed, pause = offset, 0.0
            for update in updates:
                await application.process_update(update)  # never raises: KitApplication
                offset = update.update_id + 1
            continue
        if pause:
            await _unless(stop, sleep(pause))
    # `offset` is past handled updates only: this never confirms one cut off mid-run.
    if offset != confirmed:
        await _confirm(bot, offset)
    log.info("polling stopped")


async def _unless[T](stop: asyncio.Event, aw: Awaitable[T]) -> T | None:
    """`aw`'s result, or None when `stop` is set first; `aw` is then cancelled."""
    work = asyncio.ensure_future(aw)
    stopped = asyncio.ensure_future(stop.wait())
    try:
        done, _ = await asyncio.wait((work, stopped), return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (work, stopped):
            task.cancel()  # no-op on a finished task
    if work in done:
        return work.result()
    await asyncio.gather(work, return_exceptions=True)  # let the cancelled call unwind
    return None


async def _confirm(bot: Bot, offset: int) -> None:
    """So that a clean restart does not handle the last update a second time."""
    try:
        await bot.get_updates(offset=offset, limit=1, timeout=0)
    except TelegramError:
        log.warning("last update not confirmed: the next start handles it again", exc_info=True)


def _longer(pause: float) -> float:
    return FIRST_PAUSE if pause == 0 else min(MAX_PAUSE, pause * 1.5)


def _retry_after(e: RetryAfter) -> float:
    # PTB 22 warns about the type of retry_after unless PTB_TIMEDELTA is set.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PTBDeprecationWarning)
        value = e.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else float(value)
