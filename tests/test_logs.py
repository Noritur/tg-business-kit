"""Log lines: one JSON object per line with the update's context, secrets masked
in the message and in the traceback, whichever format is on."""

import io
import json
import logging

import pytest

from kit import logs
from kit.settings import Settings, SettingsError, load_settings
from kit.storage import MemoryLeadStore

# Shaped like the real thing, so the patterns are tested against real shapes.
BOT_TOKEN = "7212345678:AAH" + "k" * 32
CASES = {
    "bot token in a Bot API URL": (
        f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        BOT_TOKEN.split(":")[1],
    ),
    "bearer token": ('Authorization: Bearer s3cr3t-api.token"', "s3cr3t-api.token"),
    "Google API key": ("key=AIza" + "B" * 35, "B" * 35),
    "password in a database URL": ("postgresql://kit:pg-Pass-123@db:5432/kit", "pg-Pass-123"),
    "Sentry DSN key": ("https://" + "a" * 32 + "@o1.ingest.sentry.io/2", "a" * 32),
}


@pytest.fixture
def store():
    return MemoryLeadStore()  # nothing here is about storage


@pytest.fixture
def capture():
    """capture(formatter) -> read(): the lines the root logger wrote."""
    root = logging.getLogger()
    handlers, level = [], root.level

    def attach(formatter: logging.Formatter):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        root.addHandler(handler)
        handlers.append(handler)
        return lambda: stream.getvalue().splitlines()

    root.setLevel(logging.INFO)
    yield attach
    for handler in handlers:
        root.removeHandler(handler)
    root.setLevel(level)


async def test_a_handler_line_names_its_update_and_business(bot, capture):
    read = capture(logs.JsonFormatter())
    update = bot.text("do you accept insurance?")
    await bot.send(update)

    lines = [json.loads(line) for line in read()]  # every line is one JSON object
    [chat] = [line for line in lines if line["msg"].startswith("chat:")]
    assert chat["logger"] == "kit.handlers.chat" and chat["level"] == "INFO"
    assert chat["update_id"] == update["update_id"] and chat["profile"] == "bright-smile-demo"
    assert chat["ts"].endswith("+00:00")
    assert logs.context() == {"update_id": None, "profile": None}  # reset on the way out


def test_profile_falls_back_to_the_process_outside_an_update(capture):
    read = capture(logs.JsonFormatter(profile="bright-smile-demo"))
    logging.getLogger("kit").info("store: memory")
    [line] = [json.loads(line) for line in read()]
    assert line["update_id"] is None and line["profile"] == "bright-smile-demo"


@pytest.mark.parametrize("formatter", [logs.TextFormatter, logs.JsonFormatter])
@pytest.mark.parametrize("case", CASES, ids=str)
def test_secret_shaped_values_are_masked_in_message_and_traceback(capture, formatter, case):
    text, secret = CASES[case]
    read = capture(formatter())
    log = logging.getLogger("kit.test")
    log.warning("call failed: %s", text)
    try:
        raise RuntimeError(text)
    except RuntimeError:
        log.exception("again")

    out = "\n".join(read())
    assert secret not in out
    assert out.count(logs.MASK) >= 2  # the message and the traceback
    if formatter is logs.JsonFormatter:
        assert all(json.loads(line) for line in read())  # masking kept every line valid


def test_ordinary_text_is_left_alone():
    text = "lead saved: id=7 service=chk 2026-10-20 09:00, see https://example.com/a@b"
    assert logs.redact(text) == text


@pytest.mark.parametrize(("value", "expected"), [("", "text"), ("json", "json")])
def test_log_format_defaults_to_text(monkeypatch, value, expected):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("LOG_FORMAT", value)
    assert load_settings(env_file=None).log_format == expected


def test_an_unknown_log_format_names_the_key(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(SettingsError, match="LOG_FORMAT"):
        load_settings(env_file=None, model=Settings)
