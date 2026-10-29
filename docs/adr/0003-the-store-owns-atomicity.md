# 3. The store owns atomicity

Status: accepted

## Context

Two clients can confirm the same slot within milliseconds, and one client could book every slot of the week. A check in the handler followed by an insert leaves a window between the two in which both clients pass the check.

## Decision

`LeadStore.save_lead` decides, in one step, whether the lead may be saved: it checks the slot's capacity and the client's number of upcoming bookings and inserts, or raises `SlotTaken` / `TooManyBookings`. In Postgres the step is a transaction that first takes `pg_advisory_xact_lock` on the business, so bookings of one business are serialised. The in-memory store does the same under an `asyncio.Lock`. The handler still checks free slots to draw the buttons, but the store has the final word. Under the same lock it first looks for a lead saved by the same confirm tap and returns that one unchecked: the tap was delivered again (ADR 4), it is not a second booking.

## Alternatives considered

- **A unique index on the slot**: covers capacity 1 only, not group slots or the per-client limit.
- **SERIALIZABLE transactions with retries**: correct and more concurrent, but every caller needs retry code. A small business books a few times an hour, so the queue the lock creates costs nothing.

## Consequences

- One rule, enforced in one place, identical in both stores; the same contract tests run against both.
- Bookings of different businesses never wait for each other.
- At thousands of bookings per minute per business the lock would become the bottleneck; that is not this product.
