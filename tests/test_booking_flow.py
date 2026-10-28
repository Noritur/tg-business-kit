import asyncio
import logging
from datetime import date

from kit import callbacks
from kit.storage import MemoryLeadStore
from tests.conftest import ADMIN_ID, CLIENT_ID, metric, new_lead, put

TUESDAY = date(2026, 10, 20)


async def _walk_to_confirm(bot, service="chk", time="09:00"):
    await bot.send(bot.click("b:svc"))
    assert callbacks.booking("s", service) in bot.last_buttons()
    await bot.send(bot.click(callbacks.booking("s", service)))
    assert callbacks.booking("d", service, TUESDAY) in bot.last_buttons()
    await bot.send(bot.click(callbacks.booking("d", service, TUESDAY)))
    assert callbacks.booking("t", service, TUESDAY, time) in bot.last_buttons()
    await bot.send(bot.click(callbacks.booking("t", service, TUESDAY, time)))
    confirm = callbacks.booking("c", service, TUESDAY, time)
    assert confirm in bot.last_buttons()
    return confirm


async def test_full_flow_saves_one_lead_and_tells_the_owner_once(bot, store, dental):
    confirm = await _walk_to_confirm(bot)
    created = metric("kit_bookings_total", result="created")
    await bot.send(bot.click(confirm))
    assert metric("kit_bookings_total", result="created") == created + 1

    [lead] = await store.recent_leads(dental.slug)
    assert (lead.service, lead.slot_date, lead.slot_time) == ("chk", TUESDAY, "09:00")
    assert lead.tg_user_id == CLIENT_ID
    [owner_note] = bot.texts_to(ADMIN_ID)
    assert owner_note.startswith("New booking") and "@olena_client" in owner_note
    text, _ = bot.last_screen()
    assert text.startswith("Done, you're booked")


async def test_a_full_slot_is_not_offered(bot, store, dental):
    await put(store, new_lead(dental, slot_date=TUESDAY, slot_time="09:00"), capacity=1)
    await bot.send(bot.click(callbacks.booking("d", "chk", TUESDAY)))
    assert callbacks.booking("t", "chk", TUESDAY, "09:00") not in bot.last_buttons()
    assert callbacks.booking("t", "chk", TUESDAY, "10:00") in bot.last_buttons()


async def test_slot_taken_while_the_client_was_confirming(bot, store, dental):
    confirm = await _walk_to_confirm(bot)
    # Someone else books the same slot between the confirm screen and the tap.
    await put(store, new_lead(dental, slot_date=TUESDAY, slot_time="09:00"), capacity=1)
    taken = metric("kit_bookings_total", result="slot_taken")
    await bot.send(bot.click(confirm))

    assert metric("kit_bookings_total", result="slot_taken") == taken + 1
    assert len(await store.recent_leads(dental.slug)) == 1
    assert bot.texts_to(ADMIN_ID) == []
    text, _ = bot.last_screen()
    assert text.startswith("That time is no longer available")


async def test_double_tap_on_confirm_books_once(bot, store, dental):
    confirm = await _walk_to_confirm(bot)
    await bot.send(bot.click(confirm))
    await bot.send(bot.click(confirm))
    assert len(await store.recent_leads(dental.slug)) == 1
    assert len(bot.texts_to(ADMIN_ID)) == 1


class SlowStore(MemoryLeadStore):
    """Yields on every read like a real database, so two handlers interleave."""

    async def taken_by_time(self, client_slug, slot_date):
        await asyncio.sleep(0)
        return await super().taken_by_time(client_slug, slot_date)


async def test_two_clients_confirming_at_once_get_one_slot(make_bot, dental, store):
    # Postgres yields on real I/O by itself; the memory store needs SlowStore for that.
    if isinstance(store, MemoryLeadStore):
        store = SlowStore()
    bot = await make_bot(dental, store=store)
    confirm = callbacks.booking("c", "chk", TUESDAY, "09:00")
    before = {r: metric("kit_bookings_total", result=r) for r in ("created", "slot_taken")}
    # Both pass the handler's free-slot check; the store has the final word.
    await asyncio.gather(
        bot.send(bot.click(confirm, user_id=CLIENT_ID)),
        bot.send(bot.click(confirm, user_id=333)),
    )

    after = {r: metric("kit_bookings_total", result=r) for r in before}
    assert {r: after[r] - before[r] for r in before} == {"created": 1, "slot_taken": 1}
    assert len(await store.recent_leads(dental.slug)) == 1
    assert len(bot.texts_to(ADMIN_ID)) == 1
    screens = [p["text"] for p in bot.calls("editMessageText")]
    assert sum(s.startswith("Done, you're booked") for s in screens) == 1
    assert sum(s.startswith("That time is no longer available") for s in screens) == 1


async def test_a_client_at_the_limit_is_told_how_to_add_more(bot, store, dental):
    for time in ("10:00", "11:00"):  # max_per_client is 2 in the preset
        await put(store, new_lead(dental, tg_user_id=CLIENT_ID, slot_date=TUESDAY, slot_time=time))
    confirm = await _walk_to_confirm(bot)
    await bot.send(bot.click(confirm))

    assert len(await store.recent_leads(dental.slug)) == 2
    assert bot.texts_to(ADMIN_ID) == []
    text, _ = bot.last_screen()
    assert text.startswith("You already have upcoming bookings") and dental.owner_contact in text


async def test_stale_or_forged_buttons_create_nothing(bot, store, dental):
    for data in [
        "b:c:chk:261019:0900",  # today: same-day booking is off
        "b:c:chk:261018:0900",  # the past
        "b:c:chk:261020:0930",  # not in the template
        "b:c:nope:261020:0900",  # no such service
    ]:
        await bot.send(bot.click(data))
    assert await store.recent_leads(dental.slug) == []
    assert bot.texts_to(ADMIN_ID) == []


async def test_owner_notification_failure_does_not_break_the_booking(bot, store, dental, caplog):
    bot.request.errors["sendMessage"] = "Forbidden: bot was blocked by the user"
    confirm = await _walk_to_confirm(bot)
    with caplog.at_level(logging.ERROR):
        await bot.send(bot.click(confirm))

    assert len(await store.recent_leads(dental.slug)) == 1
    text, _ = bot.last_screen()
    assert text.startswith("Done, you're booked")
    assert "owner notification failed" in caplog.text


async def test_store_failure_tells_the_client_how_to_reach_the_owner(bot, store, dental, caplog):
    async def broken_save(lead, capacity):
        raise ConnectionError("database is down")

    store.save_lead = broken_save
    confirm = await _walk_to_confirm(bot)
    with caplog.at_level(logging.ERROR):
        await bot.send(bot.click(confirm))

    text, _ = bot.last_screen()
    assert dental.owner_contact in text
    assert "save_lead failed" in caplog.text
