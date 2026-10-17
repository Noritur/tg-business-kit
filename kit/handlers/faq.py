"""FAQ items from the profile, one button each."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from kit import callbacks
from kit.handlers.common import button, deps, edit
from kit.handlers.menu import back_to_menu_row, book_label


async def item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    profile = deps(context).profile
    index = callbacks.parse_faq(query.data or "")
    if index is None or index >= len(profile.faq):
        return
    entry = profile.faq[index]
    rows = []
    if profile.modules.booking:
        rows.append([InlineKeyboardButton(book_label(profile), callback_data="b:svc")])
    rows.append([button(profile.t("btn_other_questions"), callbacks.menu("faq"))])
    rows.append(back_to_menu_row(profile))
    await edit(query, f"{entry.q}\n\n{entry.a}", InlineKeyboardMarkup(rows))
