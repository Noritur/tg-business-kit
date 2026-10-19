# 4. An idempotent webhook

Status: accepted

## Context

Telegram re-delivers an update when it believes the delivery failed, and a slow answer is enough for that. Processing a re-delivery again would send a second notification or a second answer. Answering with an error makes things worse: Telegram keeps retrying.

## Decision

Before handling an update the webhook records `(bot, update_id)` with `insert ... on conflict do nothing returning`; a row that already exists means the update was handled, and the request is answered 200 at once. After handling, the answer is always 200, even when a handler failed: the error goes to the log, because a retry cannot fix a bug, only repeat it. A body that is not an update is 400, a wrong or missing secret header is 403. Rows older than seven days are deleted from time to time.

## Consequences

- Re-deliveries are handled once across restarts and across several instances behind the same webhook.
- An update whose handler crashed is not retried. That is intended; the log shows it.
- Long polling needs none of this: `getUpdates` offsets already deliver each update once.
