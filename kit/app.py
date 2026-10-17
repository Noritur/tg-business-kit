"""The one place where the bot is assembled.

Polling (local) and the webhook (phase 2) both call build_app(), so handler
registration exists once. Dependencies travel in application.bot_data instead
of module globals: tests swap them per test, and nothing leaks between bots.
"""

import logging
from collections.abc import Callable
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

from kit.handlers import admin, booking, chat, faq, menu
from kit.llm import Answerer
from kit.profile import Profile
from kit.storage import LeadStore

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


# Private chats only, new messages only: edits, channel posts and group chatter
# never reach the handlers.
_PRIVATE_MESSAGES = filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE


async def _log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_id = update.update_id if isinstance(update, Update) else None
    log.error("handler failed (update_id=%s)", update_id, exc_info=context.error)


def build_app(
    deps: Deps,
    token: str,
    request: BaseRequest | None = None,
    get_updates_request: BaseRequest | None = None,
) -> Application:
    builder = Application.builder().token(token)
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
    if modules.faq:
        app.add_handler(CallbackQueryHandler(faq.item, pattern=r"^f:"))
    if modules.booking:
        app.add_handler(CallbackQueryHandler(booking.router, pattern=r"^b:"))
    app.add_handler(
        MessageHandler(_PRIVATE_MESSAGES & filters.TEXT & ~filters.COMMAND, chat.message)
    )
    # Any other button (stale, from a disabled module, forged) is acknowledged
    # so the client's spinner stops, and otherwise ignored.
    app.add_handler(CallbackQueryHandler(menu.ignore))
    app.add_error_handler(_log_error)
    return app
