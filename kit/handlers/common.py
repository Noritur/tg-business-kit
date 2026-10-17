"""Helpers shared by the handlers."""

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from kit.app import Deps

log = logging.getLogger(__name__)


def deps(context: ContextTypes.DEFAULT_TYPE) -> "Deps":
    return context.bot_data["deps"]


def button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def client_label(user: User | None) -> str:
    if user is None:
        return "?"
    name = " ".join(filter(None, [user.first_name, user.last_name])) or "?"
    handle = f"@{user.username}" if user.username else f"id {user.id}"
    return f"{name} ({handle})"


async def edit(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Edits the message behind a button; a repeated tap that changes nothing
    is not an error."""
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Owner notifications never break the client's flow, but a failure is
    logged loudly: a silent miss here is a lost client."""
    admin_id = deps(context).admin_id
    if not admin_id:
        log.warning("ADMIN_USER_ID not set, owner notification skipped")
        return False
    try:
        await context.bot.send_message(admin_id, text)
        return True
    except TelegramError:
        log.exception("owner notification failed")
        return False
