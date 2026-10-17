"""Webhook server: Telegram POSTs every update here.

    python -m kit serve --profile presets/dental-clinic.json --port 8000

The PTB Application is built once and initialised in the lifespan, not per
update. The secret travels in a header, so the URL that ends up in access logs
holds nothing worth stealing.
"""

import hmac
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.request import BaseRequest

from kit.app import Deps, build_app

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
WEBHOOK_PATH = "/telegram/webhook"


class SeenUpdates:
    """update_ids this process already handled. Telegram re-sends a delivery it
    believes failed (a timeout is enough), and a repeat must not book twice or
    answer twice. Process memory only; phase 3 moves this to Postgres."""

    def __init__(self, limit: int = 1000):
        self._ids: OrderedDict[int, None] = OrderedDict()
        self._limit = limit

    def first_time(self, update_id: int) -> bool:
        if update_id in self._ids:
            return False
        self._ids[update_id] = None
        if len(self._ids) > self._limit:
            self._ids.popitem(last=False)
        return True


def _secret_ok(sent: str, expected: str) -> bool:
    # Empty expected secret fails closed; compare_digest keeps timing flat.
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())


def create_app(
    deps: Deps,
    token: str,
    secret: str,
    request: BaseRequest | None = None,
    get_updates_request: BaseRequest | None = None,
) -> FastAPI:
    application = build_app(deps, token, request=request, get_updates_request=get_updates_request)
    seen = SeenUpdates()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await application.initialize()
        try:
            yield
        finally:
            await application.shutdown()

    # No public API yet, so no generated docs either.
    api = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @api.post(WEBHOOK_PATH)
    async def telegram_webhook(request: Request):
        if not _secret_ok(request.headers.get(SECRET_HEADER, ""), secret):
            log.warning("webhook rejected: bad or missing secret token")
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            update = Update.de_json(await request.json(), application.bot)
        except Exception:
            log.warning("webhook rejected: body is not a Telegram update")
            return JSONResponse({"error": "bad request"}, status_code=400)

        if not seen.first_time(update.update_id):
            log.info("duplicate update_id=%s skipped", update.update_id)
            return {"ok": True}
        try:
            await application.process_update(update)
        except Exception:
            log.exception("update_id=%s failed", update.update_id)
        # 200 even on failure: a retry cannot fix a bug, it only repeats it.
        return {"ok": True}

    @api.get("/health")
    async def health():
        return {"ok": True, "profile": deps.profile.slug}

    return api
