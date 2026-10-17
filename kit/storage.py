"""Lead storage behind a small async interface.

Handlers depend on `LeadStore`, not on a database. Phase 1 ships the in-memory
store for tests and local runs; the Postgres store in phase 3 keeps the same
contract, including the capacity check inside `save_lead`.
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Protocol


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
        `today` is the business-local date: leads from it on count as upcoming."""
        ...

    async def attach_phone(
        self, client_slug: str, tg_user_id: int, phone: str, since: datetime
    ) -> Lead | None:
        """Puts the phone on the client's newest lead created after `since`."""
        ...

    async def recent_leads(self, client_slug: str, limit: int = 10) -> list[Lead]: ...


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

    async def recent_leads(self, client_slug: str, limit: int = 10) -> list[Lead]:
        mine = [lead for lead in self._leads if lead.client_slug == client_slug]
        return sorted(mine, key=lambda lead: lead.created_at, reverse=True)[:limit]
