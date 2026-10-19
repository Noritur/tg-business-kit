"""The command menu behind Telegram's "Menu" button.

Telegram keeps the menu on its side, per scope: clients see the default list,
the owner sees it plus /leads in their own chat. It is set on every start, in
polling and in webhook mode alike (setMyCommands is idempotent), so a bot that
only ever ran as a webhook still gets a menu.
"""

import logging

from telegram import Bot, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import TelegramError

from kit.profile import Profile

log = logging.getLogger(__name__)


def menu_commands(profile: Profile, owner: bool = False) -> list[BotCommand]:
    t = profile.t
    names = ["start"]
    if profile.modules.booking:
        names.append("book")
    if profile.services:
        names.append("prices")
    if profile.modules.faq:
        names.append("faq")
    if owner:
        names.append("leads")
    return [BotCommand(name, t(f"cmd_{name}")) for name in names]


async def register_menu(bot: Bot, profile: Profile, admin_id: int) -> None:
    """Never fails the start-up: a bot without a menu still answers."""
    try:
        await bot.set_my_commands(menu_commands(profile), scope=BotCommandScopeDefault())
    except TelegramError:
        log.exception("client menu not set")
        return
    if not admin_id:
        log.info("menu set: %d commands, no owner menu (ADMIN_USER_ID not set)",
                 len(menu_commands(profile)))  # fmt: skip
        return
    try:
        owner = menu_commands(profile, owner=True)
        await bot.set_my_commands(owner, scope=BotCommandScopeChat(admin_id))
    except TelegramError:
        # Typically "chat not found": the owner has not opened the bot yet.
        log.exception("owner menu not set (has the owner pressed Start in the bot?)")
        return
    log.info("menu set: %d commands, owner: %d", len(menu_commands(profile)), len(owner))
