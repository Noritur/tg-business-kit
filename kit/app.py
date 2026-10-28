"""The one place where the bot is assembled.

Polling (local) and the webhook (phase 2) both call build_app(), so handler
registration exists once. Dependencies travel in application.bot_data instead
of module globals: tests swap them per test, and nothing leaks between bots.

Every update, polled or posted to the webhook, goes through
KitApplication.process_update: that is where it gets its log context and
where its result and duration are counted.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest

from kit import logs, metrics
from kit.commands import register_menu
from kit.handlers import admin, booking, chat, faq, menu
from kit.llm import Answerer
from kit.profile import Profile
from kit.storage import LeadStore, MemoryUpdateLog, UpdateLog

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    profile: Profile
    store: LeadStore
    answerer: Answerer | None
    admin_id: int  # 0 = no owner notifications
    clock: Callable[[], datetime] = field(default=utc_now)
    # Webhook claims. Polling needs none: kit.polling confirms an update to
    # Telegram only after handling it, and Telegram keeps it until then.
    updates: UpdateLog = field(default_factory=MemoryUpdateLog)


# Private chats only, new messages only: edits, channel posts and group chatter
# never reach the handlers.
_PRIVATE_MESSAGES = filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE


@dataclass
class _Run:
    """One update's processing; the error handler marks it failed."""

    failed: bool = False


_RUN: ContextVar[_Run | None] = ContextVar("run", default=None)


class KitApplication(Application):
    """Binds update_id and profile for the update's log lines, counts the
    result and the duration, and never raises: a handler's exception goes to
    the error handler below (PTB does that), and one raised outside the
    handlers is logged here. Either way it is an ERROR line, which is what
    Sentry turns into an event, and the update counts as failed.

    Handlers registered with block=False would be the exception: PTB runs
    them in a task of their own after this returns, so their failures are
    logged with the update's context and reach Sentry, but the update is
    already counted as processed. There are none today."""

    __slots__ = ()

    async def process_update(self, update: object) -> None:
        update_id = update.update_id if isinstance(update, Update) else None
        run = _Run()
        token = _RUN.set(run)
        started = time.perf_counter()
        with logs.bind(update_id=update_id, profile=self.bot_data["deps"].profile.slug):
            try:
                await super().process_update(update)
            except Exception:
                run.failed = True
                log.exception("update_id=%s failed outside the handlers", update_id)
            finally:
                _RUN.reset(token)
            seconds = time.perf_counter() - started
            result = "failed" if run.failed else "processed"
            metrics.UPDATE_SECONDS.observe(seconds)
            metrics.UPDATES.labels(result=result).inc()
            log.info("update_id=%s %s in %d ms", update_id, result, seconds * 1000)


async def _log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # The error handler is blocking (PTB's default), so it runs in the task of
    # the update that failed and sees that update's run.
    run = _RUN.get()
    if run is not None:
        run.failed = True
    update_id = update.update_id if isinstance(update, Update) else None
    log.error("handler failed (update_id=%s)", update_id, exc_info=context.error)


def build_app(
    deps: Deps,
    token: str,
    request: BaseRequest | None = None,
    get_updates_request: BaseRequest | None = None,
    post_init: Callable[[Application], Awaitable[None]] | None = None,
    post_shutdown: Callable[[Application], Awaitable[None]] | None = None,
) -> Application:
    async def _post_init(app: Application) -> None:
        if post_init is not None:
            await post_init(app)
        # kit.polling runs post_init; the webhook server calls register_menu itself.
        await register_menu(app.bot, deps.profile, deps.admin_id)

    builder = (
        Application.builder()
        .application_class(KitApplication)
        .token(token)
        .post_init(_post_init)
    )
    if post_shutdown is not None:
        builder = builder.post_shutdown(post_shutdown)
    if request is not None:
        builder = builder.request(request)
    if get_updates_request is not None:
        builder = builder.get_updates_request(get_updates_request)
    app = builder.build()
    app.bot_data["deps"] = deps

    modules = deps.profile.modules
    app.add_handler(CommandHandler("start", menu.start, filters=_PRIVATE_MESSAGES))
    app.add_handler(CommandHandler("leads", admin.leads, filters=_PRIVATE_MESSAGES))
    app.add_handler(CallbackQueryHandler(menu.router, pattern=r"^m:"))
    # The same commands kit.commands puts in the "Menu" button.
    if deps.profile.services:
        app.add_handler(
            CommandHandler("prices", menu.command("prices"), filters=_PRIVATE_MESSAGES)
        )
    if modules.faq:
        app.add_handler(CallbackQueryHandler(faq.item, pattern=r"^f:"))
        app.add_handler(CommandHandler("faq", menu.command("faq"), filters=_PRIVATE_MESSAGES))
    if modules.booking:
        app.add_handler(CallbackQueryHandler(booking.router, pattern=r"^b:"))
        app.add_handler(CommandHandler("book", booking.book_command, filters=_PRIVATE_MESSAGES))
    app.add_handler(
        MessageHandler(_PRIVATE_MESSAGES & filters.TEXT & ~filters.COMMAND, chat.message)
    )
    # Any other button (stale, from a disabled module, forged) is acknowledged
    # so the client's spinner stops, and otherwise ignored.
    app.add_handler(CallbackQueryHandler(menu.ignore))
    app.add_error_handler(_log_error)
    return app
