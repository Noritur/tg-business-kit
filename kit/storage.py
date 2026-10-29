"""Lead storage and the webhook's update log, each behind a small async interface.

Handlers depend on `LeadStore` and the web layer on `UpdateLog`, never on a
database. Two implementations of each: in-memory (tests, a quick demo) and
Postgres. The test suite runs the same contract tests against both.
"""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from time import monotonic
from typing import Protocol

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class SlotTaken(Exception):
    """The slot reached its capacity before this lead could be saved."""


class TooManyBookings(Exception):
    """The client already holds the maximum number of upcoming bookings."""


@dataclass(frozen=True)
class NewLead:
    client_slug: str
    tg_user_id: int
    tg_username: str | None
    tg_name: str | None
    service: str
    service_label: str
    slot_date: date
    slot_time: str  # "HH:MM", business timezone
    created_at: datetime
    tg_query_id: str | None  # the confirm tap; None on leads from before migration 004


@dataclass(frozen=True)
class Lead(NewLead):
    id: int = 0
    phone: str | None = None
    status: str = "new"


class LeadStore(Protocol):
    async def taken_by_time(self, client_slug: str, slot_date: date) -> dict[str, int]:
        """Active leads per "HH:MM" on that day, in one read."""
        ...

    async def save_lead(
        self, lead: NewLead, capacity: int, max_per_client: int, today: date
    ) -> Lead:
        """Saves the lead or raises SlotTaken / TooManyBookings. Checks and
        insert are one step: two clients confirming the same slot at once must
        not both get it, and one client must not hold the whole calendar.
        `today` is the business-local date: leads from it on count as upcoming.
        A lead already saved for `lead.tg_query_id` is returned as it is, with
        no checks: that is the same tap delivered again, not a new booking."""
        ...

    async def lead_by_query(self, client_slug: str, tg_query_id: str) -> Lead | None:
        """The lead a confirm tap created, if it created one."""
        ...

    async def attach_phone(
        self, client_slug: str, tg_user_id: int, phone: str, since: datetime
    ) -> Lead | None:
        """Puts the phone on the client's newest lead created after `since`."""
        ...

    async def recent_leads(
        self, client_slug: str, limit: int = 10, before_id: int | None = None
    ) -> list[Lead]:
        """Newest first; `before_id` continues a page (a cursor, not an offset)."""
        ...


class Delivery(StrEnum):
    """What one delivery of an update is, as the update log sees it."""

    NEW = "new"  # never seen: handle it
    ABANDONED = "abandoned"  # an attempt never finished within STALE_AFTER: handle it again
    IN_PROGRESS = "in_progress"  # an attempt is running: Telegram should retry later
    DONE = "done"  # handled already: skip it


# Longer than any handler run (Gemini retries across three models included),
# or a slow update gets a second attempt next to the first. Also the longest a
# crashed update waits before a re-delivery handles it again.
STALE_AFTER = timedelta(minutes=5)


class UpdateLog(Protocol):
    async def claim(self, bot: str, update_id: int) -> Delivery:
        """Starts an attempt unless one is running or the update is done. After
        NEW or ABANDONED the caller handles the update, then calls finish()."""
        ...

    async def finish(self, bot: str, update_id: int) -> None:
        """Marks the update handled: every later delivery is DONE."""
        ...


# --- in memory -----------------------------------------------------------------


class MemoryLeadStore:
    """Process-local store: fine for tests and a demo run, gone on restart."""

    def __init__(self) -> None:
        self._leads: list[Lead] = []
        self._lock = asyncio.Lock()

    def _active(self, client_slug: str) -> list[Lead]:
        return [
            lead
            for lead in self._leads
            if lead.client_slug == client_slug and lead.status != "cancelled"
        ]

    async def taken_by_time(self, client_slug: str, slot_date: date) -> dict[str, int]:
        counts: dict[str, int] = {}
        for lead in self._active(client_slug):
            if lead.slot_date == slot_date:
                counts[lead.slot_time] = counts.get(lead.slot_time, 0) + 1
        return counts

    async def save_lead(
        self, lead: NewLead, capacity: int, max_per_client: int, today: date
    ) -> Lead:
        async with self._lock:
            if lead.tg_query_id is not None and (
                saved := await self.lead_by_query(lead.client_slug, lead.tg_query_id)
            ):
                return saved
            active = self._active(lead.client_slug)
            holding = [
                other
                for other in active
                if other.tg_user_id == lead.tg_user_id and other.slot_date >= today
            ]
            if len(holding) >= max_per_client:
                raise TooManyBookings(f"user {lead.tg_user_id} holds {len(holding)}")
            same_slot = [
                other
                for other in active
                if other.slot_date == lead.slot_date and other.slot_time == lead.slot_time
            ]
            if len(same_slot) >= capacity:
                raise SlotTaken(f"{lead.slot_date} {lead.slot_time}")
            saved = Lead(**vars(lead), id=len(self._leads) + 1)
            self._leads.append(saved)
            return saved

    async def lead_by_query(self, client_slug: str, tg_query_id: str) -> Lead | None:
        for lead in self._leads:
            if lead.client_slug == client_slug and lead.tg_query_id == tg_query_id:
                return lead
        return None

    async def attach_phone(
        self, client_slug: str, tg_user_id: int, phone: str, since: datetime
    ) -> Lead | None:
        async with self._lock:
            for i in range(len(self._leads) - 1, -1, -1):
                lead = self._leads[i]
                if (
                    lead.client_slug == client_slug
                    and lead.tg_user_id == tg_user_id
                    and lead.created_at >= since
                ):
                    self._leads[i] = replace(lead, phone=phone)
                    return self._leads[i]
            return None

    async def recent_leads(
        self, client_slug: str, limit: int = 10, before_id: int | None = None
    ) -> list[Lead]:
        mine = [
            lead
            for lead in self._leads
            if lead.client_slug == client_slug and (before_id is None or lead.id < before_id)
        ]
        return sorted(mine, key=lambda lead: lead.id, reverse=True)[:limit]


class MemoryUpdateLog:
    """The last `limit` update_ids per process. A restart forgets the claims
    with everything else, so the re-delivery after a crash is simply NEW."""

    def __init__(self, limit: int = 1000, stale_after: timedelta = STALE_AFTER):
        # (bot, update_id) -> monotonic start of the running attempt, None once done
        self._claims: OrderedDict[tuple[str, int], float | None] = OrderedDict()
        self._limit = limit
        self._stale_after = stale_after.total_seconds()

    async def claim(self, bot: str, update_id: int) -> Delivery:
        key, now = (bot, update_id), monotonic()
        if key not in self._claims:
            delivery = Delivery.NEW
        elif (started := self._claims[key]) is None:
            return Delivery.DONE
        elif now - started < self._stale_after:
            return Delivery.IN_PROGRESS
        else:
            delivery = Delivery.ABANDONED
        self._claims[key] = now
        if len(self._claims) > self._limit:
            self._claims.popitem(last=False)
        return delivery

    async def finish(self, bot: str, update_id: int) -> None:
        if (bot, update_id) in self._claims:
            self._claims[(bot, update_id)] = None


# --- Postgres --------------------------------------------------------------------


_LEAD_COLUMNS = (
    "id, created_at, client_slug, tg_user_id, tg_username, tg_name, service, service_label,"
    " slot_date, to_char(slot_time, 'HH24:MI') as slot_time, phone, status, tg_query_id"
)
_BY_QUERY = f"select {_LEAD_COLUMNS} from leads where client_slug = %s and tg_query_id = %s"


def _lead(row: dict) -> Lead:
    return Lead(**row)


class PostgresLeadStore:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def taken_by_time(self, client_slug: str, slot_date: date) -> dict[str, int]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select to_char(slot_time, 'HH24:MI'), count(*) from leads"
                " where client_slug = %s and slot_date = %s and status <> 'cancelled'"
                " group by slot_time",
                (client_slug, slot_date),
            )
            return {slot: count for slot, count in await cur.fetchall()}

    async def save_lead(
        self, lead: NewLead, capacity: int, max_per_client: int, today: date
    ) -> Lead:
        async with self._pool.connection() as conn, conn.transaction():
            # One booking per business at a time: the count and the insert below
            # cannot interleave with another client's. Bookings of a small
            # business arrive a few per hour, so the queue costs nothing; a
            # SERIALIZABLE transaction with retries would work too, at the price
            # of retry code in every caller.
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"kit:leads:{lead.client_slug}",),
            )
            if lead.tg_query_id is not None:
                # Under the lock as well: an attempt that outlived its claim and
                # the one that took it over cannot both insert.
                cur = conn.cursor(row_factory=dict_row)
                await cur.execute(_BY_QUERY, (lead.client_slug, lead.tg_query_id))
                if row := await cur.fetchone():
                    return _lead(row)
            cur = await conn.execute(
                "select"
                " count(*) filter (where tg_user_id = %s and slot_date >= %s),"
                " count(*) filter (where slot_date = %s and slot_time = %s)"
                " from leads where client_slug = %s and status <> 'cancelled'",
                (lead.tg_user_id, today, lead.slot_date, lead.slot_time, lead.client_slug),
            )
            holding, same_slot = await cur.fetchone()
            if holding >= max_per_client:
                raise TooManyBookings(f"user {lead.tg_user_id} holds {holding}")
            if same_slot >= capacity:
                raise SlotTaken(f"{lead.slot_date} {lead.slot_time}")
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "insert into leads (created_at, client_slug, tg_user_id, tg_username, tg_name,"
                " service, service_label, slot_date, slot_time, tg_query_id)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                f" returning {_LEAD_COLUMNS}",
                (
                    lead.created_at,
                    lead.client_slug,
                    lead.tg_user_id,
                    lead.tg_username,
                    lead.tg_name,
                    lead.service,
                    lead.service_label,
                    lead.slot_date,
                    time.fromisoformat(lead.slot_time),
                    lead.tg_query_id,
                ),
            )
            return _lead(await cur.fetchone())

    async def lead_by_query(self, client_slug: str, tg_query_id: str) -> Lead | None:
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(_BY_QUERY, (client_slug, tg_query_id))
            row = await cur.fetchone()
            return _lead(row) if row else None

    async def attach_phone(
        self, client_slug: str, tg_user_id: int, phone: str, since: datetime
    ) -> Lead | None:
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "update leads set phone = %s where id = ("
                " select id from leads"
                " where client_slug = %s and tg_user_id = %s and created_at >= %s"
                " order by created_at desc, id desc limit 1)"
                f" returning {_LEAD_COLUMNS}",
                (phone, client_slug, tg_user_id, since),
            )
            row = await cur.fetchone()
            return _lead(row) if row else None

    async def recent_leads(
        self, client_slug: str, limit: int = 10, before_id: int | None = None
    ) -> list[Lead]:
        async with self._pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                f"select {_LEAD_COLUMNS} from leads"
                " where client_slug = %s and (%s::bigint is null or id < %s)"
                " order by id desc limit %s",
                (client_slug, before_id, before_id, limit),
            )
            return [_lead(row) for row in await cur.fetchall()]


class PostgresUpdateLog:
    """Survives restarts and is shared by every instance behind the webhook."""

    CLEANUP_EVERY = 500  # claims
    KEEP = "7 days"  # Telegram stops re-delivering long before this

    def __init__(self, pool: AsyncConnectionPool, stale_after: timedelta = STALE_AFTER):
        self._pool = pool
        self._stale_after = stale_after
        self._claims = 0

    async def claim(self, bot: str, update_id: int) -> Delivery:
        async with self._pool.connection() as conn:
            # A new row, or a stale attempt taken over. Of two deliveries at once
            # one waits for the other's row lock, then sees its fresh claimed_at
            # and gets no row back.
            cur = await conn.execute(
                "insert into processed_updates as p (bot, update_id) values (%s, %s)"
                " on conflict (bot, update_id) do update set claimed_at = now()"
                " where p.done_at is null and p.claimed_at <= now() - %s"
                # Both default to now() on insert; a take-over moves claimed_at only.
                " returning p.received_at = p.claimed_at",
                (bot, update_id, self._stale_after),
            )
            claimed = await cur.fetchone()
            if claimed is not None:
                delivery = Delivery.NEW if claimed[0] else Delivery.ABANDONED
            else:
                cur = await conn.execute(
                    "select done_at is not null from processed_updates"
                    " where bot = %s and update_id = %s",
                    (bot, update_id),
                )
                done = await cur.fetchone()
                delivery = Delivery.DONE if done is not None and done[0] else Delivery.IN_PROGRESS
            self._claims += 1
            if self._claims % self.CLEANUP_EVERY == 0:
                await conn.execute(
                    "delete from processed_updates where received_at < now() - %s::interval",
                    (self.KEEP,),
                )
            return delivery

    async def finish(self, bot: str, update_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "update processed_updates set done_at = now() where bot = %s and update_id = %s",
                (bot, update_id),
            )
