"""kit.polling against a Bot API that keeps updates the way Telegram does.

Telegram holds an update until a getUpdates call asks for an offset past it,
so "confirmed" below means exactly that call. A killed process is a cancelled
poll task: nothing after the cancellation point runs, as with kill -9.
"""

import asyncio
import contextlib
import json
import logging

import pytest
from telegram.error import BadRequest, InvalidToken, NetworkError, TimedOut

from kit.app import Deps, build_app
from kit.polling import poll, serve
from kit.storage import MemoryLeadStore
from tests.conftest import (
    ADMIN_ID,
    CLIENT_ID,
    MONDAY_NOON,
    TOKEN,
    BotHarness,
    FakeAnswerer,
    FakeTelegramRequest,
)

BUPA = "We accept Bupa."


class FakeTelegram(FakeTelegramRequest):
    """getUpdates as Telegram answers it: an update stays until a call asks for
    an offset past it, and a call with nothing to return waits for an update
    up to its timeout. `failures` are the next calls' answers instead: an
    exception to raise or a (status, body) pair."""

    def __init__(self):
        super().__init__()
        self.pending: list[dict] = []
        self.offsets: list[int] = []
        self.failures: list = []
        self._arrived = asyncio.Event()

    def push(self, update: dict) -> None:
        self.pending.append(update)
        self._arrived.set()

    def held(self) -> list[int]:
        return [u["update_id"] for u in self.pending]

    async def do_request(self, url, method, request_data=None, **timeouts):
        if not url.endswith("/getUpdates"):
            return await super().do_request(url, method, request_data, **timeouts)
        await asyncio.sleep(0)
        params = dict(request_data.parameters) if request_data else {}
        offset = params.get("offset") or 0
        self.offsets.append(offset)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            status, body = failure
            return status, json.dumps(body).encode()
        if offset:
            self.pending = [u for u in self.pending if u["update_id"] >= offset]
        if not self.pending and params.get("timeout"):
            self._arrived.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), params["timeout"])
        batch = self.pending[: params.get("limit") or 100]
        return 200, json.dumps({"ok": True, "result": batch}).encode()


class SlowAnswerer(FakeAnswerer):
    """A Gemini call that returns once `release` is set."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def answer(self, profile, question):
        self.entered.set()
        await self.release.wait()
        return await super().answer(profile, question)


def _error(status: int, description: str, **parameters) -> tuple[int, dict]:
    body = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return status, body


async def until(condition, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def make_bot(telegram, dental):
    """make_bot(answerer, **build_app kwargs) -> BotHarness whose getUpdates
    goes to `telegram`. Several bots share it: a restart is a second bot."""
    apps = []

    def _make(answerer, initialized: bool = True, **kwargs):
        async def make():
            deps = Deps(
                profile=dental,
                store=MemoryLeadStore(),
                answerer=answerer,
                admin_id=ADMIN_ID,
                clock=lambda: MONDAY_NOON,
            )
            request = FakeTelegramRequest()
            app = build_app(deps, TOKEN, request=request, get_updates_request=telegram, **kwargs)
            if initialized:
                await app.initialize()
            apps.append(app)
            return BotHarness(app, request, deps)

        return make()

    yield _make
    for app in apps:
        await app.shutdown()


def start(bot: BotHarness, stop: asyncio.Event, **kwargs) -> asyncio.Task:
    return asyncio.create_task(poll(bot.app, stop, timeout=kwargs.pop("timeout", 0.05), **kwargs))


async def test_an_update_is_confirmed_only_after_its_handler_returned(make_bot, telegram):
    slow = SlowAnswerer()
    bot = await make_bot(slow)
    telegram.push(bot.text("do you take Bupa?"))
    stop = asyncio.Event()
    task = start(bot, stop)

    await slow.entered.wait()
    await asyncio.sleep(0.1)  # time enough for further polls, if any were made
    assert telegram.held() == [1]
    assert max(telegram.offsets) < 2

    slow.release.set()
    await until(lambda: telegram.held() == [])
    stop.set()
    await task
    assert BUPA in bot.texts_to(CLIENT_ID)[0]


async def test_a_killed_process_leaves_its_update_and_the_backlog_with_telegram(make_bot, telegram):
    slow = SlowAnswerer()
    bot = await make_bot(slow)
    telegram.push(bot.text("do you take Bupa?"))
    task = start(bot, asyncio.Event())
    await slow.entered.wait()
    # Two more updates arrive while the first one is still being answered.
    telegram.push(bot.text("/start"))
    telegram.push(bot.text("/start"))
    await asyncio.sleep(0.1)

    task.cancel()  # kill -9
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert telegram.held() == [1, 2, 3]
    assert bot.texts_to(CLIENT_ID) == []

    restarted = await make_bot(FakeAnswerer())
    stop = asyncio.Event()
    task = start(restarted, stop)
    await until(lambda: telegram.held() == [])
    stop.set()
    await task
    replies = restarted.texts_to(CLIENT_ID)
    assert len(replies) == 3
    assert BUPA in replies[0]


async def test_a_killed_process_replays_only_the_update_it_was_handling(make_bot, telegram):
    slow = SlowAnswerer()
    bot = await make_bot(slow)
    for text in ("/start", "do you take Bupa?", "/start"):
        telegram.push(bot.text(text))
    task = start(bot, asyncio.Event())
    await slow.entered.wait()  # update 2; update 1 is answered already

    task.cancel()  # kill -9
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # A batch would be confirmed whole: update 1 would come back and be answered twice.
    assert telegram.held() == [2, 3]
    assert len(bot.texts_to(CLIENT_ID)) == 1


async def test_a_stop_lets_the_running_update_finish_and_confirms_it(make_bot, telegram):
    slow = SlowAnswerer()
    bot = await make_bot(slow)
    telegram.push(bot.text("do you take Bupa?"))
    stop = asyncio.Event()
    task = start(bot, stop)
    await slow.entered.wait()
    telegram.push(bot.text("/start"))

    stop.set()  # SIGTERM
    await asyncio.sleep(0.05)
    assert not task.done()  # the running update is not cut off

    slow.release.set()
    await asyncio.wait_for(task, 2)
    assert telegram.offsets[-1] == 2  # the handled update is confirmed on the way out
    assert telegram.held() == [2]  # the next one waits for the next start
    assert [BUPA in t for t in bot.texts_to(CLIENT_ID)] == [True]


async def test_a_stop_while_waiting_for_updates_returns_at_once(make_bot, telegram):
    bot = await make_bot(FakeAnswerer())
    stop = asyncio.Event()
    task = start(bot, stop, timeout=30)
    await until(lambda: telegram.offsets)

    stop.set()
    await asyncio.wait_for(task, 1)
    assert telegram.offsets == [0]  # nothing handled, nothing to confirm


@pytest.mark.parametrize(
    ("failures", "pauses"),
    [
        ([NetworkError("connection reset"), NetworkError("connection reset")], [1.0, 1.5]),
        ([TimedOut()], []),
        pytest.param(
            [_error(429, "Too Many Requests: retry after 7", retry_after=7)],
            [7.5],
            # PTB's own RetryAfter constructor warns about the attribute's future type.
            marks=pytest.mark.filterwarnings("ignore::telegram.warnings.PTBDeprecationWarning"),
        ),
        ([_error(400, "Bad Request: something new")], [1.0]),
    ],
    ids=["network", "timed_out", "flood_control", "other"],
)
async def test_a_failed_poll_is_retried_after_a_pause(make_bot, telegram, failures, pauses):
    bot = await make_bot(FakeAnswerer())
    telegram.failures = list(failures)
    telegram.push(bot.text("/start"))
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    stop = asyncio.Event()
    task = start(bot, stop, sleep=sleep)
    await until(lambda: bot.texts_to(CLIENT_ID))
    stop.set()
    await task
    assert slept == pauses


@pytest.mark.parametrize(
    ("failure", "logged"),
    [
        (
            _error(409, "Conflict: terminated by other getUpdates request"),
            "another process polls with this token",
        ),
        ((200, {"ok": True, "result": [1]}), "getUpdates answer not processed"),
    ],
    ids=["conflict", "unreadable_answer"],
)
async def test_a_poll_failure_nobody_fixes_by_waiting_is_an_error(
    make_bot, telegram, caplog, failure, logged
):
    bot = await make_bot(FakeAnswerer())
    telegram.failures = [failure]
    telegram.push(bot.text("/start"))

    async def sleep(_):
        pass

    stop = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger="kit.polling"):
        task = start(bot, stop, sleep=sleep)
        await until(lambda: bot.texts_to(CLIENT_ID))
        stop.set()
        await task
    assert logged in caplog.text


async def test_an_invalid_token_stops_polling(make_bot, telegram):
    bot = await make_bot(FakeAnswerer())
    telegram.failures = [_error(401, "Unauthorized")]
    with pytest.raises(InvalidToken):
        await asyncio.wait_for(poll(bot.app, asyncio.Event(), timeout=0.05), 2)


async def test_serve_runs_the_application_the_way_run_polling_did(make_bot, telegram):
    shut_down = []

    async def post_shutdown(_app):
        shut_down.append(True)

    bot = await make_bot(FakeAnswerer(), initialized=False, post_shutdown=post_shutdown)
    stop = asyncio.Event()
    task = asyncio.create_task(serve(bot.app, stop))
    await until(lambda: telegram.offsets)
    stop.set()
    await asyncio.wait_for(task, 2)

    sent = [endpoint for endpoint, _ in bot.request.calls]
    assert "deleteWebhook" in sent  # getUpdates is refused while a webhook is set
    assert "setMyCommands" in sent  # post_init: the menu
    assert shut_down == [True]


async def test_a_start_that_cannot_delete_the_webhook_polls_nothing(make_bot, telegram):
    shut_down = []

    async def post_shutdown(_app):
        shut_down.append(True)

    bot = await make_bot(FakeAnswerer(), initialized=False, post_shutdown=post_shutdown)
    bot.request.errors["deleteWebhook"] = "Bad Request: not now"
    with pytest.raises(BadRequest):
        await asyncio.wait_for(serve(bot.app, asyncio.Event()), 2)
    assert telegram.offsets == []
    assert shut_down == [True]
