# 4. An idempotent webhook

Status: accepted; revised with migration 003, when the log became claims, and 004, when a lead learned the tap that created it

## Context

Telegram re-delivers an update when it believes the delivery failed, and a slow answer is enough for that. Handling a re-delivery again would send a second notification or a second answer. The first attempt can also die with the process (out of memory, kill -9, a deploy): then nobody answered Telegram, the update was not handled, and the re-delivery is the only chance it has. The first version of this decision recorded the update before handling it and took any recorded update as handled, so exactly that re-delivery was skipped and the update lost.

## Decision

A row in `processed_updates` is a claim until its `done_at` is set. Each delivery calls `claim(bot, update_id)`, a single upsert, and gets one of four answers:

- **new**: no row yet. The update is handled, `finish` sets `done_at`, the answer is 200.
- **done**: handled before. 200 at once, nothing runs.
- **in progress**: claimed less than five minutes ago and not done. 503, so Telegram tries again later; by then the attempt has either finished (done) or died (abandoned).
- **abandoned**: claimed five minutes ago or more and never done. The claim is taken over and the update handled as if it were new, with a WARNING line saying so.

`done_at` is set once `process_update` returns, also when a handler failed: the error goes to the log, because a retry cannot fix a bug, only repeat it. It is not set when the process dies or the request is cancelled, which is the case retries are for. If `finish` itself fails, the answer is still 200 and the error is logged: a 500 would bring a re-delivery that, past the window, handles the update twice. A failing `claim` is a 500, so Telegram retries once the database is back; a body that is not an update is 400, a wrong or missing secret header is 403. Rows older than seven days are deleted from time to time.

Two deliveries at once are settled by the database: the second upsert waits for the first one's row lock, then sees a fresh claim and gets in progress.

## Consequences

- At least once instead of at most once: a crash no longer loses the update, provided Telegram still retries after the window. Telegram promises retries for any answer other than 2xx but documents no schedule.
- A replay runs the handler from the start, so whatever the dead attempt already did happens again unless the handler can tell. The booking handler can: a lead keeps the `callback_query.id` of the confirm tap that created it (migration 004, unique per business), and a re-delivery carries the same id. A replayed confirmation finds its lead before the free-slot check, which would count the lead against itself, and finishes what the dead attempt started: the owner's note and the client's "booked" screen, counted as `kit_bookings_total{result="replayed"}`. The note arrives twice if the dead attempt had sent it already. Telegram refuses to answer a tap that old; the booking handler logs that and carries on, the other buttons still fail on it with an ERROR line, which costs the client one navigation step.
- The window has to outlast any handler run; a run that takes longer gets a second attempt next to it. The longest run today is an answer from Gemini after retries across three models. Each call is cut off after 20 s (`CALL_TIMEOUT_SECONDS` in `kit/llm.py`), so that run takes at most 3 models x 2 attempts x 20 s plus 13.5 s of backoff, 133.5 s, under half the window; a test keeps it there.
- A slow answer shows up in `getWebhookInfo` as a 503 in `last_error_message`, and in `kit_updates_total{result="in_progress"}`.
- Not chosen: a lock or transaction held for the whole run, which would notice a dead process at once. It keeps a pool connection busy per running update while the handlers need connections of their own, so a burst of updates would exhaust the pool.
- The in-memory log loses its claims with the process, so after a crash the re-delivery is simply new.
- Long polling needs no claims: Telegram keeps an update until `getUpdates` confirms it, and `kit.polling` confirms it only after handling it ([ADR 8](0008-polling-confirms-after-handling.md)).
