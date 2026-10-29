"""Button-driven booking: service -> day -> time -> confirm -> lead.

Every step re-checks what the button claims. A button can be days old, point
to a slot someone else just took, or be forged, so availability is decided
here and in the store, never trusted from callback_data.
"""

import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from kit import callbacks, metrics
from kit.handlers.common import answer, button, client_label, deps, edit, notify_owner
from kit.handlers.menu import back_to_menu_row
from kit.profile import Profile, Service
from kit.slots import is_bookable, local_today, template_times, upcoming_days
from kit.storage import Lead, NewLead, SlotTaken, TooManyBookings

log = logging.getLogger(__name__)


def _service_markup(profile: Profile) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(s.label, callback_data=callbacks.booking("s", s.code))]
        for s in profile.services
    ]
    rows.append(back_to_menu_row(profile))
    return InlineKeyboardMarkup(rows)


async def book_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/book: the first booking step as a new message."""
    message = update.effective_message
    if message is None:
        return
    profile = deps(context).profile
    await message.reply_text(profile.t("choose_service"), reply_markup=_service_markup(profile))


async def _free_times(context: ContextTypes.DEFAULT_TYPE, day: date) -> list[str]:
    d = deps(context)
    taken = await d.store.taken_by_time(d.profile.slug, day)  # one read for the whole day
    capacity = d.profile.booking.slot_capacity
    return [t for t in template_times(d.profile, day) if taken.get(t, 0) < capacity]


async def _show_days(query, context, service: Service, notice: str | None = None) -> None:
    d = deps(context)
    profile, t = d.profile, d.profile.t
    days = upcoming_days(profile, d.clock())
    if not days:
        markup = InlineKeyboardMarkup([back_to_menu_row(profile)])
        await edit(query, t("no_slots"), markup)
        return
    rows = [
        [InlineKeyboardButton(t.day(day), callback_data=callbacks.booking("d", service.code, day))]
        for day in days
    ]
    rows.append([InlineKeyboardButton(t("btn_back"), callback_data="b:svc")])
    text = t("choose_day", city=profile.city)
    await edit(query, f"{notice}\n\n{text}" if notice else text, InlineKeyboardMarkup(rows))


async def _booked(query, context, lead: Lead) -> None:
    """Tells the owner, then shows the client their booking."""
    profile = deps(context).profile
    t = profile.t
    day_label = t.day(lead.slot_date, with_year=True)
    await notify_owner(
        context,
        t(
            "owner_new_lead",
            service=lead.service_label,
            day=day_label,
            time=lead.slot_time,
            client=client_label(query.from_user),
        ),
    )
    await edit(
        query,
        t("booked", service=lead.service_label, day=day_label, time=lead.slot_time),
        InlineKeyboardMarkup([back_to_menu_row(profile)]),
    )


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await answer(query)
    d = deps(context)
    profile, t = d.profile, d.profile.t

    if query.data == callbacks.booking_start():
        await edit(query, t("choose_service"), _service_markup(profile))
        return

    step = callbacks.parse_booking(query.data or "")
    service = profile.service(step.service) if step else None
    if step is None or service is None:
        return
    now = d.clock()

    if step.step == "s":
        await _show_days(query, context, service)
        return

    if step.step == "d":
        in_window = step.day in upcoming_days(profile, now)
        times = await _free_times(context, step.day) if in_window else []
        if not times:
            await _show_days(query, context, service, notice=t("slot_unavailable"))
            return
        rows = [[button(tm, callbacks.booking("t", service.code, step.day, tm))] for tm in times]
        rows.append([button(t("btn_back"), callbacks.booking("s", service.code))])
        await edit(query, t("choose_time", day=t.day(step.day)), InlineKeyboardMarkup(rows))
        return

    # The same tap delivered again after the attempt handling it died: the lead
    # may be saved already, and then the checks below would count it against
    # itself. Finish what the dead attempt started instead.
    if step.step == "c" and (saved := await d.store.lead_by_query(profile.slug, query.id)):
        metrics.BOOKINGS.labels(result="replayed").inc()
        log.warning("lead replayed: id=%s", saved.id)
        await _booked(query, context, saved)
        return

    if not is_bookable(profile, step.day, step.time, now) or step.time not in await _free_times(
        context, step.day
    ):
        if step.step == "c":
            # Taken before the tap reached the store; the race is counted below.
            metrics.BOOKINGS.labels(result="slot_taken").inc()
        await _show_days(query, context, service, notice=t("slot_unavailable"))
        return

    if step.step == "t":
        day_label = t.day(step.day, with_year=True)
        confirm = callbacks.booking("c", service.code, step.day, step.time)
        rows = [
            [InlineKeyboardButton(t("btn_confirm"), callback_data=confirm)],
            [
                InlineKeyboardButton(
                    t("btn_other_time"), callback_data=callbacks.booking("s", service.code)
                )
            ],
        ]
        text = t("confirm", service=service.label, day=day_label, time=step.time)
        await edit(query, text, InlineKeyboardMarkup(rows))
        return

    # step "c": the store decides, atomically, whether the slot is still free.
    user = query.from_user
    lead = NewLead(
        client_slug=profile.slug,
        tg_user_id=user.id,
        tg_username=user.username,
        tg_name=" ".join(filter(None, [user.first_name, user.last_name])) or None,
        service=service.code,
        service_label=service.label,
        slot_date=step.day,
        slot_time=step.time,
        created_at=now,
        tg_query_id=query.id,
    )
    try:
        saved = await d.store.save_lead(
            lead,
            capacity=profile.booking.slot_capacity,
            max_per_client=profile.booking.max_per_client,
            today=local_today(profile, now),
        )
    except SlotTaken:
        metrics.BOOKINGS.labels(result="slot_taken").inc()
        await _show_days(query, context, service, notice=t("slot_unavailable"))
        return
    except TooManyBookings:
        metrics.BOOKINGS.labels(result="too_many").inc()
        markup = InlineKeyboardMarkup([back_to_menu_row(profile)])
        await edit(query, t("too_many_bookings", contact=profile.owner_contact), markup)
        return
    except Exception:
        metrics.BOOKINGS.labels(result="error").inc()
        log.exception("save_lead failed")
        await edit(query, t("save_failed", contact=profile.owner_contact))
        return

    metrics.BOOKINGS.labels(result="created").inc()
    log.info("lead saved: id=%s service=%s %s %s", saved.id, service.code, step.day, step.time)
    await _booked(query, context, saved)
