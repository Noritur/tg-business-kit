"""Errors to Sentry, without secrets and without clients' personal data.

Off unless SENTRY_DSN is set: then sentry_sdk.init is never called and the
bot sends nothing anywhere. When on, every ERROR log line becomes an event.
That is the only way the webhook's failures reach Sentry at all: the webhook
answers 200 even when a handler crashed (ADR 0004), so the FastAPI
integration never sees those exceptions.

What would leak by default, and what stops it. DATA_COLLECTION turns off
every category the SDK collects on its own; the rest is before_send:
- the request body, an update with names and phone numbers: Sentry keeps up
  to 10 KB of it by default -> http_bodies=[] and max_request_body_size
  "never", and before_send drops request data anyway;
- the webhook secret header: Sentry's own list of sensitive headers misses
  X-Telegram-Bot-Api-Secret-Token -> before_send drops it, Authorization
  and Cookie by name;
- the bot token in frame locals (create_app's `token` argument) ->
  stack_frame_variables=False;
- Bot API URLs in httpx breadcrumbs -> the httpx integration is off, and
  http breadcrumbs are dropped;
- client questions sent to Gemini -> gen_ai off, the google-genai
  integration off;
- an Update in a library's log arguments (PTB logs one when it cannot build
  a context) -> events from other libraries' loggers keep the template only;
- anything else shaped like a secret -> kit.logs.redact over every string.
"""

import logging

import sentry_sdk
from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

from kit import __version__, logs
from kit.settings import Settings

log = logging.getLogger(__name__)

DROP_HEADERS = {"x-telegram-bot-api-secret-token", "authorization", "cookie"}
# sentry-sdk's data_collection (it replaces send_default_pii): every category
# off or filtered. Header values stay for debugging, minus the SDK's denylist
# (names with secret, token, auth, ...) and the client-IP headers.
DATA_COLLECTION = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {
        "request": {"mode": "denylist", "terms": ["forwarded", "-ip", "remote-", "via", "-user"]}
    },
    "http_bodies": [],
    "url_query_params": {"mode": "denylist"},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": False,
}


def setup(settings: Settings, profile: str | None = None, transport=None) -> bool:
    """Starts Sentry if SENTRY_DSN is set. `transport` is for tests."""
    if settings.sentry_dsn is None:
        log.info("sentry: off (SENTRY_DSN not set)")
        return False
    environment = settings.sentry_environment or "production"
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=environment,
        release=settings.sentry_release or f"tg-business-kit@{__version__}",
        data_collection=DATA_COLLECTION,
        max_request_body_size="never",
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        disabled_integrations=[HttpxIntegration, GoogleGenAIIntegration],
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        transport=transport,
    )
    # A request that crashes is reported by the FastAPI integration; uvicorn
    # logging the same traceback would make it two events.
    ignore_logger("uvicorn.error")
    if profile:
        sentry_sdk.get_global_scope().set_tag("profile", profile)
    log.info("sentry: on (environment=%s)", environment)
    return True


def _ours(logger: str) -> bool:
    return logger == "kit" or logger.startswith("kit.")


def before_send(event: dict, hint: dict) -> dict:
    request = event.get("request")
    if request:
        request.pop("data", None)
        request.pop("cookies", None)
        headers = request.get("headers") or {}
        request["headers"] = {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS}

    logentry = event.get("logentry")
    if logentry and not _ours(event.get("logger") or ""):
        # Our log arguments are ids and counts; a library's can be whole objects.
        logentry.pop("params", None)
        logentry["formatted"] = logentry.get("message", "")

    tags = event.setdefault("tags", {})
    for name, value in logs.context().items():
        if value is not None:
            tags[name] = str(value)
    return _redacted(event)


def before_breadcrumb(crumb: dict, hint: dict) -> dict | None:
    if crumb.get("type") == "http" or crumb.get("category") in ("httplib", "httpx"):
        return None  # a Bot API URL carries the token
    return _redacted(crumb)


def _redacted(value):
    if isinstance(value, str):
        return logs.redact(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redacted(item) for item in value]
    return value
