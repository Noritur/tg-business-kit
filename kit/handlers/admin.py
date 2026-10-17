"""Owner-only commands."""

from telegram import Update
from telegram.ext import ContextTypes

from kit.handlers.common import deps


async def leads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user = update.effective_message, update.effective_user
    d = deps(context)
    # Silence for everyone else: the command should not even look like it exists.
    if msg is None or user is None or not d.admin_id or user.id != d.admin_id:
        return
    profile, t = d.profile, d.profile.t
    rows = await d.store.recent_leads(profile.slug)
    if not rows:
        await msg.reply_text(t("leads_empty"))
        return
    lines = [t("leads_title", count=len(rows)), ""]
    for lead in rows:
        who = lead.tg_name or f"id {lead.tg_user_id}"
        if lead.tg_username:
            who = f"@{lead.tg_username}"
        phone = f", {lead.phone}" if lead.phone else ""
        lines.append(
            f"{t.day(lead.slot_date)} {lead.slot_time} — {lead.service_label} — "
            f"{who}{phone} [{lead.status}]"
        )
    await msg.reply_text("\n".join(lines))
