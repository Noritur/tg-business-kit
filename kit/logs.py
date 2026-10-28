"""Log lines: text for a terminal, JSON for a log collector, no secrets in either.

    LOG_FORMAT=json   {"ts", "level", "logger", "msg", "exc"?, "update_id", "profile"}

update_id and profile are context variables that KitApplication binds around
every update, so a line written deep inside a handler still says which update
and which business it belongs to. Concurrent updates run in their own asyncio
tasks, each with its own copy of the context, and bind() resets on the way out.

Redaction is the second line of defence; ADR 0006 is the first. Whatever is
shaped like a bot token, a bearer token, a Google API key or the user part of
a URL (a database password, a Sentry DSN key) is masked in the message and in
the traceback. So are the exact values of the secrets the process holds:
the generated ones (the webhook secret, the API and metrics tokens) are
random strings no pattern could recognise.
"""

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

UPDATE_ID: ContextVar[int | None] = ContextVar("update_id", default=None)
PROFILE: ContextVar[str | None] = ContextVar("profile", default=None)
_FIELDS = {"update_id": UPDATE_ID, "profile": PROFILE}

MASK = "[redacted]"
_SECRETS = [
    # A bot token, alone or inside a Bot API URL (.../bot<token>/sendMessage).
    (re.compile(r"\d+:[A-Za-z0-9_-]{30,}"), MASK),
    # RFC 6750 token characters only, so a closing quote is never swallowed.
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), rf"\1{MASK}"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), MASK),
    # user:password@ in a database URL, the key@ of a Sentry DSN.
    (re.compile(r"://[^/@\s]+@"), f"://{MASK}@"),
]
# The values of the secrets in use, longest first; setup() fills it in.
_KNOWN: list[str] = []
# A shorter value would mask ordinary words; real secrets are far longer.
_MIN_KNOWN = 8


def redact(text: str) -> str:
    for value in _KNOWN:
        text = text.replace(value, MASK)
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


@contextmanager
def bind(**fields):
    """Sets update_id and/or profile for every line written inside the block."""
    tokens = [(_FIELDS[name], _FIELDS[name].set(value)) for name, value in fields.items()]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def context() -> dict:
    return {name: var.get() for name, var in _FIELDS.items()}


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    """One JSON object per line. `profile` fills in for lines written outside
    an update: one process serves one business (ADR 0001)."""

    def __init__(self, profile: str | None = None) -> None:
        super().__init__()
        self.profile = profile

    def format(self, record: logging.LogRecord) -> str:
        fields = context()
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            # Redacted field by field: json.dumps then escapes whatever is left.
            "msg": redact(record.getMessage()),
            "update_id": fields["update_id"],
            "profile": fields["profile"] or self.profile,
        }
        if record.exc_info and record.exc_info[0] is not None:
            line["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(line, ensure_ascii=False)


def setup(fmt: str = "text", profile: str | None = None, secrets=()) -> None:
    """`secrets`: their values are masked from now on (Settings.secret_values)."""
    _KNOWN[:] = sorted({s for s in secrets if len(s) >= _MIN_KNOWN}, key=len, reverse=True)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(profile) if fmt == "json" else TextFormatter())
    # force: whatever configured the root logger first (one module-level
    # logging.warning() is enough) would keep its own format and no masking.
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx logs every Bot API URL, and those URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # google-genai announces "AFC is enabled" on every call.
    logging.getLogger("google_genai").setLevel(logging.WARNING)
