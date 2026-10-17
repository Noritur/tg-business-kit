import asyncio
from datetime import timedelta

import pytest

from kit.storage import SlotTaken, TooManyBookings
from tests.conftest import MONDAY_NOON, new_lead

TODAY = MONDAY_NOON.date()


def save(store, lead, capacity=1, max_per_client=100):
    return store.save_lead(lead, capacity=capacity, max_per_client=max_per_client, today=TODAY)


async def test_save_refuses_a_full_slot(store, dental):
    await save(store, new_lead(dental))
    with pytest.raises(SlotTaken):
        await save(store, new_lead(dental, tg_user_id=333))


async def test_capacity_above_one_allows_a_group(store, dental):
    for user_id in (1, 2):
        await save(store, new_lead(dental, tg_user_id=user_id), capacity=2)
    with pytest.raises(SlotTaken):
        await save(store, new_lead(dental, tg_user_id=3), capacity=2)


async def test_concurrent_saves_of_one_slot_let_exactly_one_through(store, dental):
    results = await asyncio.gather(
        *(save(store, new_lead(dental, tg_user_id=i)) for i in range(5)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, SlotTaken) for r in results) == 4


async def test_one_client_cannot_hold_the_whole_calendar(store, dental):
    for time in ("09:00", "10:00"):
        await save(store, new_lead(dental, tg_user_id=7, slot_time=time), max_per_client=2)
    with pytest.raises(TooManyBookings):
        await save(store, new_lead(dental, tg_user_id=7, slot_time="11:00"), max_per_client=2)


async def test_past_bookings_do_not_count_against_the_limit(store, dental):
    yesterday = TODAY - timedelta(days=1)
    await save(store, new_lead(dental, tg_user_id=7, slot_date=yesterday), max_per_client=1)
    await save(store, new_lead(dental, tg_user_id=7), max_per_client=1)


async def test_taken_by_time_counts_one_day_in_one_read(store, dental):
    tuesday = TODAY + timedelta(days=1)
    for user_id, time in ((1, "09:00"), (2, "09:00"), (3, "10:00")):
        await save(store, new_lead(dental, tg_user_id=user_id, slot_time=time), capacity=2)
    await save(store, new_lead(dental, slot_date=tuesday + timedelta(days=1)))
    assert await store.taken_by_time(dental.slug, tuesday) == {"09:00": 2, "10:00": 1}


async def test_phone_goes_to_the_newest_lead_inside_the_window(store, dental):
    two_days_ago = MONDAY_NOON - timedelta(days=2)
    await save(store, new_lead(dental, tg_user_id=7, slot_time="10:00", created_at=two_days_ago))
    await save(store, new_lead(dental, tg_user_id=7, slot_time="11:00"))

    since = MONDAY_NOON - timedelta(days=1)
    lead = await store.attach_phone(dental.slug, 7, "+34600000000", since)
    assert (lead.slot_time, lead.phone) == ("11:00", "+34600000000")
    assert await store.attach_phone(dental.slug, 8, "+34600000000", MONDAY_NOON) is None


async def test_recent_leads_are_per_business_and_newest_first(store, dental, school):
    for hours in (3, 1, 2):
        created = MONDAY_NOON - timedelta(hours=hours)
        await save(store, new_lead(dental, slot_time=f"1{hours}:00", created_at=created))
    await save(store, new_lead(school))

    leads = await store.recent_leads(dental.slug, limit=2)
    assert [lead.slot_time for lead in leads] == ["11:00", "12:00"]
