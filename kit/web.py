"""Webhook server and the owner's API.

    python -m kit serve --profile presets/dental-clinic.json --port 8000

POST /telegram/webhook   Telegram delivers updates; secret header, fail-closed
GET  /api/leads          the owner's bookings; bearer token, cursor pagination
GET  /health             liveness, nothing secret
GET  /api/docs           OpenAPI for /api (the webhook is left out of the schema)

The PTB Application is built once and initialised in the lifespan, not per
update. The webhook secret travels in a header, so access logs hold nothing
worth stealing.
"""

import hmac
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from telegram import Update
from telegram.request import BaseRequest

from kit.app import Deps, build_app
from kit.commands import register_menu

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
WEBHOOK_PATH = "/telegram/webhook"


def _same(sent: str, expected: str) -> bool:
    # An empty expected value fails closed; compare_digest keeps timing flat.
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())


class LeadOut(BaseModel):
    id: int
    created_at: datetime
    service: str
    service_label: str
    slot_date: date
    slot_time: str
    tg_username: str | None
    tg_name: str | None
    phone: str | None
    status: str


class LeadPage(BaseModel):
    leads: list[LeadOut]
    next_before_id: int | None  # pass as before_id for the next page; null = last page


def create_app(
    deps: Deps,
    token: str,
    secret: str,
    request: BaseRequest | None = None,
    get_updates_request: BaseRequest | None = None,
    api_token: str = "",
    on_startup: Callable[[], Awaitable[None]] | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    application = build_app(deps, token, request=request, get_updates_request=get_updates_request)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if on_startup is not None:
            await on_startup()
        await application.initialize()
        await register_menu(application.bot, deps.profile, deps.admin_id)
        try:
            yield
        finally:
            await application.shutdown()
            if on_shutdown is not None:
                await on_shutdown()

    api = FastAPI(
        title="tg-business-kit",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @api.post(WEBHOOK_PATH, include_in_schema=False)
    async def telegram_webhook(request: Request):
        if not _same(request.headers.get(SECRET_HEADER, ""), secret):
            log.warning("webhook rejected: bad or missing secret token")
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            update = Update.de_json(await request.json(), application.bot)
        except Exception:
            log.warning("webhook rejected: body is not a Telegram update")
            return JSONResponse({"error": "bad request"}, status_code=400)

        # Telegram re-sends a delivery it believes failed (a timeout is enough).
        if not await deps.updates.first_time(deps.profile.slug, update.update_id):
            log.info("duplicate update_id=%s skipped", update.update_id)
            return {"ok": True}
        try:
            await application.process_update(update)
        except Exception:
            log.exception("update_id=%s failed", update.update_id)
        # 200 even on failure: a retry cannot fix a bug, it only repeats it.
        return {"ok": True}

    async def owner_only(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if not api_token:
            raise HTTPException(status_code=404)  # switched off until a token is configured
        if credentials is None or not _same(credentials.credentials, api_token):
            raise HTTPException(
                status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )

    @api.get("/api/leads", response_model=LeadPage, dependencies=[Depends(owner_only)])
    async def list_leads(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        before_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> LeadPage:
        """The business's bookings, newest first."""
        leads = await deps.store.recent_leads(deps.profile.slug, limit=limit, before_id=before_id)
        return LeadPage(
            leads=[LeadOut.model_validate(lead, from_attributes=True) for lead in leads],
            next_before_id=leads[-1].id if len(leads) == limit else None,
        )

    @api.get("/health")
    async def health():
        return {"ok": True, "profile": deps.profile.slug}

    return api
