"""/start, the main menu and its screens."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from kit import callbacks
from kit.handlers.common import button, deps, edit
from kit.profile import Profile
from kit.slots import upcoming_days

log = logging.getLogger(__name__)


def book_label(profile: Profile) -> str:
    trial = profile.booking.trial if profile.booking else None
    return trial.label if trial and trial.enabled else profile.t("btn_book")


def main_menu_markup(profile: Profile) -> InlineKeyboardMarkup:
    t = profile.t
    rows = []
    if profile.modules.booking:
        rows.append([InlineKeyboardButton(book_label(profile), callback_data="b:svc")])
    info_row = []
    if profile.services:
        info_row.append(button(t("btn_prices"), callbacks.menu("prices")))
    if profile.modules.booking:
        info_row.append(button(t("btn_schedule"), callbacks.menu("sched")))
    if info_row:
        rows.append(info_row)
    if profile.modules.faq:
        rows.append([InlineKeyboardButton(t("btn_faq"), callback_data=callbacks.menu("faq"))])
    return InlineKeyboardMarkup(rows)


def greeting(profile: Profile) -> str:
    return profile.t("greeting", name=profile.name, tagline=profile.tagline, about=profile.about)


def back_to_menu_row(profile: Profile) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(profile.t("btn_back_menu"), callback_data=callbacks.menu("home"))]


def book_and_back(profile: Profile) -> InlineKeyboardMarkup:
    rows = []
    if profile.modules.booking:
        rows.append([InlineKeyboardButton(book_label(profile), callback_data="b:svc")])
    rows.append(back_to_menu_row(profile))
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    profile = deps(context).profile
    log.info("start: user_id=%s", update.effective_user.id if update.effective_user else None)
    await message.reply_text(greeting(profile), reply_markup=main_menu_markup(profile))


def screen(profile: Profile, name: str, now) -> tuple[str, InlineKeyboardMarkup]:
    """Text and buttons of a menu screen. Buttons edit the message in place,
    commands send it as a new one; both take the screen from here."""
    t = profile.t
    # A screen of a disabled module falls back to the main menu.
    if (name == "sched" and not profile.modules.booking) or (
        name == "faq" and not profile.modules.faq
    ):
        name = "home"

    if name == "prices":
        lines = [t("prices_title"), ""]
        for s in profile.services:
            lines.append(f"{s.label} — {s.price}")
            if s.note:
                lines.append(f"  {s.note}")
        if profile.booking and profile.booking.trial.enabled:
            lines += ["", profile.booking.trial.label]
        return "\n".join(lines), book_and_back(profile)
    if name == "sched":
        days = upcoming_days(profile, now)
        lines = [t("schedule_title"), ""] + [t.day(day) for day in days]
        lines += ["", t("schedule_note", city=profile.city)]
        return "\n".join(lines), book_and_back(profile)
    if name == "faq":
        rows = [
            [InlineKeyboardButton(item.q, callback_data=callbacks.faq(i))]
            for i, item in enumerate(profile.faq)
        ]
        rows.append(back_to_menu_row(profile))
        return t("faq_title"), InlineKeyboardMarkup(rows)
    return greeting(profile), main_menu_markup(profile)


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    d = deps(context)
    name = callbacks.parse_menu(query.data or "")
    if name is None:
        return
    await edit(query, *screen(d.profile, name, d.clock()))


def command(name: str):
    """A /command that opens a menu screen as a new message."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return
        d = deps(context)
        text, markup = screen(d.profile, name, d.clock())
        await message.reply_text(text, reply_markup=markup)

    handler.__name__ = f"{name}_command"
    return handler


async def ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    log.info("ignored callback: %r", (query.data or "")[:64])
    await query.answer()
