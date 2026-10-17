"""Free text: a phone number right after booking, otherwise an AI answer.

Whatever the bot cannot answer from the profile goes to the owner. The reply
promises that, so the forwarding is not optional.
"""

import logging
import re
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from kit import callbacks
from kit.handlers.common import client_label, deps, notify_owner
from kit.handlers.menu import book_label
from kit.llm import Answer
from kit.profile import Profile

log = logging.getLogger(__name__)

# Loose international phone shape: optional +, digits with spaces, dashes, brackets.
PHONE_RE = re.compile(r"^\+?[\d\s\-()]{9,18}$")
PHONE_WINDOW = timedelta(hours=24)


def cta_markup(profile: Profile) -> InlineKeyboardMarkup:
    rows = []
    if profile.modules.booking:
        rows.append([InlineKeyboardButton(book_label(profile), callback_data="b:svc")])
    rows.append([InlineKeyboardButton(profile.t("btn_menu"), callback_data=callbacks.menu("home"))])
    return InlineKeyboardMarkup(rows)


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.effective_message, update.effective_user
    if msg is None or user is None or not msg.text:
        return
    d = deps(context)
    profile, t = d.profile, d.profile.t
    text = msg.text.strip()
    # Length only: client messages are personal data and stay out of the logs.
    log.info("chat: user_id=%s len=%d", user.id, len(text))

    if profile.modules.booking and PHONE_RE.match(text):
        phone = re.sub(r"[^\d+]", "", text)
        try:
            lead = await d.store.attach_phone(
                profile.slug, user.id, phone, since=d.clock() - PHONE_WINDOW
            )
        except Exception:
            log.exception("attach_phone failed")
            lead = None
        if lead is not None:
            await msg.reply_text(t("phone_saved"))
            await notify_owner(context, t("owner_phone", client=client_label(user), phone=phone))
            return

    answer: Answer | None = None
    if profile.modules.ai_answers and d.answerer is not None:
        try:
            answer = await d.answerer.answer(profile, text)
        except Exception:
            log.exception("LLM failed for user_id=%s", user.id)

    if answer is None or answer.handoff:
        await notify_owner(context, t("owner_question", client=client_label(user), question=text))
    reply = answer.reply.strip() if answer and answer.reply.strip() else t("handoff_reply")
    await msg.reply_text(reply, reply_markup=cta_markup(profile))
