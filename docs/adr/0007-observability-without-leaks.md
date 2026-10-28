# 7. Observability without leaks

Status: accepted

## Context

A bot that answers 200 even when a handler crashed (ADR 0004) fails quietly unless someone reads the logs. Logs, metrics and an error tracker fix that, but each is a new place for data to go, and the data here is sensitive. Every Bot API URL contains the token. The webhook request carries the secret header. The body of an update holds clients' names and phone numbers. A frame of `create_app` holds the raw token as a local variable. Error trackers collect request bodies, headers, frame locals and HTTP breadcrumbs by default, and an error tracker's list of sensitive headers does not know Telegram's.

## Decision

- **Logs** are one JSON object per line with `LOG_FORMAT=json` (compose sets it), text otherwise. `update_id` and `profile` are context variables bound around every update in `KitApplication.process_update`, so every line inside a handler names its update. Masking is a second line of defence behind ADR 0006: bot tokens, bearer tokens, Google API keys and the user part of URLs are replaced in messages and tracebacks, and so are the exact values of the secrets the process holds, because the generated ones are random strings no pattern recognises. `logs.setup` takes over the root logger (`force=True`): a handler configured earlier would keep its own format and no masking. Client messages are still logged by length only. uvicorn's access log is off: the healthcheck and the scrape would fill it.
- **Metrics** live in one registry and are served at `GET /metrics` behind their own bearer token, `METRICS_TOKEN`. The endpoint is off without it, because the TLS proxy in front of the webhook forwards every path. A separate token means whoever scrapes counters cannot read bookings. Label values come from fixed sets only: never a user id, a path or text. The webhook server is one uvicorn process, so `prometheus_client`'s multiprocess mode is not needed. Long polling counts too but exposes nothing.
- **Sentry** is off without `SENTRY_DSN`; `init` is then never called. When on, every ERROR log line is an event, which is how handled failures reach it. Collection is configured to nothing by default (`data_collection`: no bodies, cookies, user info, frame variables or Gemini inputs). `before_send` drops the secret header, `Authorization` and `Cookie`, and masks secrets everywhere. Log messages keep only their template, for every logger, in events and in breadcrumbs: our arguments are user ids, a library's can be a whole Update, and a breadcrumb travels with the next event. The httpx integration is off (its breadcrumbs carry Bot API URLs), and so is the google-genai integration (its spans carry client questions). The environment is `development` unless set, and compose sets `production`; the release carries the commit (`GIT_SHA`, a build argument), so two builds of one version are told apart.
- Each rule has a test that puts the secret or the phone number in and checks it does not come out: in log lines, in events, in `/metrics`.

## Alternatives considered

- **OpenTelemetry for all three**: one standard, but a collector to run and more moving parts than one bot per business needs. The JSON log fields and the metric names map onto it later if that changes.
- **Scrubbing only in Sentry's project settings**: works after the data has left the server. Here nothing leaves that should not.
- **`/metrics` on a second port**: avoids the token, but a second published port and a proxy rule to keep it private. A token is one line in the scrape config.

## Consequences

- A failure is an ERROR line, an event with `update_id` and `profile` tags, and `kit_updates_total{result="failed"}`; the client still gets an answer and Telegram a 200.
- A database outage during deduplication is the one 500: it is counted as failed, logged inside the update's context so the event has its update id, and Telegram retries the update later. `kit_updates_total` counts deliveries, so that update then counts as processed too.
- Debugging data is thinner on purpose: no request bodies, no locals, no log arguments. The update id in the event leads to the log lines of that update, which have the values.
- Handlers with `block=False` would run after the update is counted: their failures reach the log and Sentry but not `kit_updates_total{result="failed"}`. There are none.
