# 8. Long polling confirms an update after handling it

Status: accepted

## Context

Telegram keeps an update until a `getUpdates` call asks for an offset past it, and forgets it on that call. PTB's `Updater`, which `run_polling` uses, sends that offset as soon as a batch is queued: the next poll goes out at once and carries it, while the handlers of the batch have not run. On shutdown it confirms everything fetched before it drains the queue, and docker sends SIGKILL ten seconds after SIGTERM. A process killed mid-handler (out of memory, kill -9, a deploy during a slow Gemini answer) lost the update it was handling and every update queued behind it, and the restart received nothing. Compose runs the bot by long polling, so this was the production path. Against a fake Bot API that keeps Telegram's offset rules, the old `kit run` lost all three updates of the test, one in a handler and two waiting, both to kill -9 and to SIGTERM followed by SIGKILL.

## Decision

`kit.polling` replaces `run_polling`, and PTB's `Updater` is not used. It asks for one update at a time (`limit=1`), hands it to `process_update`, and only then moves the offset past it, so the next `getUpdates` is the confirmation. The application runs in `run_polling`'s order: `initialize`, `post_init`, `deleteWebhook`, polling, `shutdown`, `post_shutdown`.

SIGINT and SIGTERM stop the loop between updates: a running update finishes, one more `getUpdates` confirms it, and the process exits; a long poll that is waiting is cancelled. Failed polls are retried the way PTB retried them: an invalid token ends the process, flood control waits as long as Telegram asks, a timeout is retried at once, anything else after a pause that grows from 1 to 30 seconds. A 409 (another process polls with the token, or a webhook is set) is an ERROR line, because waiting does not fix it.

## Consequences

- At least once, as with the webhook (ADR 4): an update a dying process did not finish stays with Telegram and is handled on the next start, the SIGKILL after a grace period included.
- A replay runs the handler from the start, with the same consequences as a webhook replay in ADR 4. An update also runs twice if the process dies after its handler returned and before the next `getUpdates`, a window of one request.
- One update per call, because Telegram confirms a batch whole: a crash would replay the updates of the batch already handled. The cost is a round trip per update, nothing at this bot's volume. Updates are handled one at a time, as under PTB's default.
- No claims in `processed_updates` for polling: Telegram holds an update until it is confirmed and answers 409 to a second poller, so no update runs in two processes at once.
- An update whose handler kills the process every time comes back on every start, and `restart: unless-stopped` turns that into a crash loop; before, it was dropped without a trace. Nothing breaks the loop automatically.
- Not chosen: claims in `processed_updates`. A claim cannot bring back an update Telegram has already forgotten unless the raw update, the client's text included, is stored with it, and it would have to be written before the next poll, inside the `Updater`.
- Not chosen: the `Updater` with an offset held back until the handler finishes. Telegram returns unconfirmed updates on every poll, so they would be queued again and again, and the long poll would return at once, in a loop, for as long as a slow handler runs.
