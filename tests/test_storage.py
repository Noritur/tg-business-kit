"""The LeadStore and UpdateLog contracts. The `store` and `updates` fixtures are
parametrized, so every test here runs on memory and on Postgres."""

import asyncio
from collections import Counter
from datetime import timedelta

import pytest

from kit.storage import (
    Delivery,
    MemoryUpdateLog,
    PostgresUpdateLog,
    SlotTaken,
    TooManyBookings,
)
from tests.conftest import MONDAY_NOON, new_lead

TODAY = MONDAY_NOON.date()


def save(store, lead, capacity=1, max_per_client=100):
    return store.save_lead(lead, capacity=capacity, max_per_client=max_per_client, today=TODAY)


async def test_saved_lead_comes_back_with_an_id_and_the_same_fields(store, dental):
    lead = new_lead(dental, slot_time="09:30")
    saved = await save(store, lead)
    assert saved.id > 0 and (saved.phone, saved.status) == (None, "new")
    for field in ("client_slug", "tg_user_id", "service", "slot_date", "slot_time", "created_at"):
        assert getattr(saved, field) == getattr(lead, field)


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


async def test_slots_are_counted_per_business(store, dental, school):
    await save(store, new_lead(dental))
    await save(store, new_lead(school))  # same day and time, another business


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


async def test_recent_leads_are_per_business_newest_first_and_page_by_id(store, dental, school):
    for time in ("10:00", "11:00", "12:00"):
        await save(store, new_lead(dental, slot_time=time))
    await save(store, new_lead(school))

    first = await store.recent_leads(dental.slug, limit=2)
    assert [lead.slot_time for lead in first] == ["12:00", "11:00"]
    rest = await store.recent_leads(dental.slug, limit=2, before_id=first[-1].id)
    assert [lead.slot_time for lead in rest] == ["10:00"]


@pytest.fixture(params=["memory", "postgres"])
def stale_updates(request):
    """An update log with a zero window: every unfinished claim is already past it."""
    if request.param == "memory":
        return MemoryUpdateLog(stale_after=timedelta(0))
    return PostgresUpdateLog(request.getfixturevalue("pg_pool"), stale_after=timedelta(0))


async def test_update_log_handles_each_update_once_per_bot(updates):
    assert await updates.claim("bot-a", 42) is Delivery.NEW
    await updates.finish("bot-a", 42)
    assert await updates.claim("bot-a", 42) is Delivery.DONE
    assert await updates.claim("bot-b", 42) is Delivery.NEW


async def test_a_redelivery_while_the_attempt_runs_is_in_progress(updates):
    assert await updates.claim("bot-a", 42) is Delivery.NEW
    assert await updates.claim("bot-a", 42) is Delivery.IN_PROGRESS
    await updates.finish("bot-a", 42)
    assert await updates.claim("bot-a", 42) is Delivery.DONE


async def test_an_attempt_that_never_finished_is_handled_again_past_the_window(stale_updates):
    # The process died mid-handler: a claim and no finish.
    assert await stale_updates.claim("bot-a", 42) is Delivery.NEW
    assert await stale_updates.claim("bot-a", 42) is Delivery.ABANDONED
    await stale_updates.finish("bot-a", 42)
    # Done stays done, however old the claim.
    assert await stale_updates.claim("bot-a", 42) is Delivery.DONE


async def test_concurrent_deliveries_of_one_update_start_one_attempt(updates):
    results = await asyncio.gather(*(updates.claim("bot-a", 42) for _ in range(5)))
    assert Counter(results) == {Delivery.NEW: 1, Delivery.IN_PROGRESS: 4}


async def test_postgres_update_log_survives_a_restart(pg_pool):
    first = PostgresUpdateLog(pg_pool)
    assert await first.claim("bot-a", 7) is Delivery.NEW
    await first.finish("bot-a", 7)
    # A new process: a fresh object over the same database.
    assert await PostgresUpdateLog(pg_pool).claim("bot-a", 7) is Delivery.DONE


async def test_postgres_claim_of_a_killed_process_is_taken_over_past_the_window(pg_pool):
    assert await PostgresUpdateLog(pg_pool).claim("bot-a", 7) is Delivery.NEW
    # Killed mid-handler. The restarted process waits out the window, then takes over.
    assert await PostgresUpdateLog(pg_pool).claim("bot-a", 7) is Delivery.IN_PROGRESS
    restarted = PostgresUpdateLog(pg_pool, stale_after=timedelta(0))
    assert await restarted.claim("bot-a", 7) is Delivery.ABANDONED
