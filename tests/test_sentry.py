"""Sentry events: one per failure, and none of the secrets or the personal data
an update carries. A test transport collects the events; nothing leaves the
process (and the network guard in conftest would fail the test if it tried).
"""

import json
import logging
import time

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from kit import sentry as kit_sentry
from kit.app import Deps
from kit.settings import Settings
from kit.storage import MemoryLeadStore
from kit.web import SECRET_HEADER, WEBHOOK_PATH, create_app
from tests.conftest import ADMIN_ID, CLIENT_ID, MONDAY_NOON, TOKEN, FakeTelegramRequest

SECRET = "test-webhook-secret"
DSN = "https://" + "0" * 32 + "@o1.ingest.sentry.io/1"
PHONE = "+380671234567"
BOT_TOKEN = "7212345678:AAH" + "k" * 32  # shaped like a real one, for redact()


class Collect(Transport):
    def __init__(self):
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


def _settings(**env) -> Settings:
    return Settings(_env_file=None, telegram_bot_token=TOKEN, **env)


@pytest.fixture
def events():
    transport = Collect()
    assert kit_sentry.setup(
        _settings(sentry_dsn=DSN, sentry_environment="test"),
        profile="bright-smile-demo",
        transport=transport,
    )
    yield transport.events
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)  # back to a client that records nothing


@pytest.fixture
def store():
    return MemoryLeadStore()


@pytest.fixture
def client(dental, store, answerer, events):
    deps = Deps(
        profile=dental, store=store, answerer=answerer, admin_id=ADMIN_ID,
        clock=lambda: MONDAY_NOON,
    )  # fmt: skip
    api = create_app(deps, TOKEN, SECRET, FakeTelegramRequest(), FakeTelegramRequest())
    with TestClient(api) as client:
        yield client


def _update(text: str, user_id: int = CLIENT_ID, update_id: int = 1) -> dict:
    message = {
        "message_id": update_id,
        "date": int(time.time()),
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Olena", "last_name": "Kovalenko"},
        "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
    return {"update_id": update_id, "message": message}


# Everything a request to the webhook carries that must not reach Sentry.
HEADERS = {SECRET_HEADER: SECRET, "Authorization": "Bearer owner-token", "Cookie": "session=abc"}


async def _down(*args, **kwargs):
    raise ConnectionError("database is down")


def _payload(event: dict) -> str:
    """The event as Sentry receives it, minus the source lines around each
    frame: those are the repo's own code (this file included, with its test
    values), public anyway."""
    event = json.loads(json.dumps(event))
    for value in event.get("exception", {}).get("values", []):
        for frame in value.get("stacktrace", {}).get("frames", []):
            for key in ("pre_context", "context_line", "post_context"):
                frame.pop(key, None)
    return json.dumps(event)


def test_a_crashing_handler_is_one_event_without_the_request_secrets(client, store, events):
    store.recent_leads = _down
    response = client.post(
        WEBHOOK_PATH, json=_update("/leads", user_id=ADMIN_ID, update_id=41), headers=HEADERS
    )
    assert response.status_code == 200

    [event] = events
    assert event["logger"] == "kit.app" and event["level"] == "error"
    assert event["environment"] == "test" and event["release"] == "tg-business-kit@0.1.0"
    assert event["tags"]["update_id"] == "41" and event["tags"]["profile"] == "bright-smile-demo"
    request = event["request"]  # the FastAPI integration did attach the request
    assert request["url"].endswith(WEBHOOK_PATH)
    assert not {name.lower() for name in request["headers"]} & kit_sentry.DROP_HEADERS
    assert "data" not in request and "cookies" not in request

    [exception] = event["exception"]["values"]
    assert exception["type"] == "ConnectionError"
    frames = exception["stacktrace"]["frames"]
    assert frames and not any("vars" in frame for frame in frames)

    raw = _payload(event)
    for value in (TOKEN, SECRET, "owner-token", "session=abc", "Olena", "Kovalenko"):
        assert value not in raw


def test_a_phone_in_the_update_never_reaches_sentry(client, store, events):
    store.attach_phone = _down
    client.post(WEBHOOK_PATH, json=_update(PHONE), headers=HEADERS)

    [event] = events  # "attach_phone failed"; the bot then answered as usual
    assert event["logentry"]["message"] == "attach_phone failed"
    assert "671234567" not in _payload(event)


def test_library_log_arguments_stay_out_of_sentry(events):
    # The shape PTB uses when it cannot build a context for an update.
    logging.getLogger("telegram.ext.Application").critical(
        "Error while building CallbackContext for update %s", {"phone": PHONE}
    )
    [event] = events
    assert event["logentry"]["message"].startswith("Error while building CallbackContext")
    assert PHONE not in _payload(event)


def test_secret_shaped_values_are_masked_in_events_and_breadcrumbs(events, caplog):
    caplog.set_level(logging.INFO)  # INFO is the level `run` and `serve` log at
    log = logging.getLogger("kit.test")
    log.info("calling https://api.telegram.org/bot%s/getMe", BOT_TOKEN)  # a breadcrumb
    try:
        raise RuntimeError(f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage failed")
    except RuntimeError:
        log.exception("Bot API call failed")

    [event] = events
    assert BOT_TOKEN.split(":")[1] not in _payload(event)
    assert any("getMe" in crumb["message"] for crumb in event["breadcrumbs"]["values"])


def test_without_a_dsn_sentry_is_never_initialised(monkeypatch):
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *args, **kwargs: calls.append(kwargs))
    assert kit_sentry.setup(_settings(sentry_dsn="")) is False
    assert calls == []
