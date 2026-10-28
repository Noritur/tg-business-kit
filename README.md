# tg-business-kit

[![ci](https://github.com/Noritur/tg-business-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/Noritur/tg-business-kit/actions/workflows/ci.yml)

A Telegram bot engine for small service businesses — clinics, language schools, beauty studios, tutors. Each business is one JSON profile: services, weekly slots, FAQ and UI texts. The engine turns it into a button-driven booking flow, an FAQ menu and AI answers grounded strictly in the profile. Whatever the profile does not answer goes to the owner, in Telegram.

Two demo profiles ship with it: a dental clinic (English) and an online language school (Ukrainian). All businesses in them are fictional.

## What it does

- **Booking**: service → day → time → confirm, in the business's timezone. Slot capacity and a per-client limit are enforced by the database, so two clients tapping the same slot at once cannot both get it.
- **Owner in the loop**: new bookings, phone numbers and every question the bot could not answer arrive in the owner's chat.
- **AI answers from the profile only**: Gemini with structured output (`{reply, handoff}`) and a model fallback chain. No invented prices; unknown questions are handed off, not guessed.
- **Command menu**: `/start /book /prices /faq` for clients, plus `/leads` for the owner, set on every start.
- **Owner API**: `GET /api/leads` with a bearer token and cursor pagination; OpenAPI at `/api/docs`.
- **Two ways to run**: long polling (no public URL needed) or a FastAPI webhook server.
- **Observability**: JSON logs with the update's id, Prometheus metrics behind a token, Sentry with secret and personal-data scrubbing.

## Architecture

```mermaid
flowchart LR
    profile[profile.json] --> app
    tg[Telegram] -- long polling --> app
    tg -- webhook + secret header --> web[FastAPI /telegram/webhook]
    web -- update_id claimed, then done --> app
    app[build_app: handlers] --> store[(Postgres: leads)]
    app --> llm[Gemini: answers from the profile]
    app -- notifications --> owner[Owner's chat]
    api[GET /api/leads] --> store
    prom[Prometheus] -- bearer token --> metrics[GET /metrics]
    app -- ERROR lines --> sentry[Sentry, scrubbed]
```

Handlers depend on two small interfaces, `LeadStore` and `UpdateLog`, with an in-memory and a Postgres implementation of each. Dependencies travel in the bot's `bot_data`, not in module globals.

## A new business in 15 minutes

```bash
cp presets/dental-clinic.json presets/my-clinic.json     # name, services, weekly slots, FAQ
uv run python -m kit validate presets/my-clinic.json     # unknown fields, broken times, typos in texts
uv run python scripts/setup_env.py                       # bot token, Gemini key, owner id (hidden input)
uv run python scripts/setup_env.py --generated-only      # database password, API token
KIT_PROFILE=presets/my-clinic.json docker compose up -d --build
```

`docker compose up` starts Postgres, runs the migrations as a one-shot service and starts the bot only after they exit 0.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest                                            # Postgres half skipped without a database

docker compose --profile test up -d testdb
TEST_DATABASE_URL=postgresql://kit:kit@127.0.0.1:5434/kit_test uv run pytest   # both halves
```

## Webhook mode

```bash
docker compose --profile webhook up -d --build           # webhook server on 127.0.0.1:8000
uv run python scripts/set_webhook.py https://<your-host>/telegram/webhook
```

The port is bound to localhost on purpose; put a TLS reverse proxy in front of it. Tools for a running server:

```bash
uv run python scripts/synthetic_update.py http://127.0.0.1:8000/telegram/webhook "hi"
uv run python scripts/synthetic_update.py http://127.0.0.1:8000/telegram/webhook --no-secret   # 403
uv run python scripts/webhook_info.py                    # what Telegram reports about the webhook
uv run python scripts/commands_info.py                   # the Menu button, for clients and the owner
uv run python scripts/api_leads.py http://127.0.0.1:8000 # GET /api/leads with ADMIN_API_TOKEN
```

## Observability

```bash
uv run python scripts/setup_env.py --generated-only      # adds METRICS_TOKEN
uv run python scripts/setup_env.py --sentry              # Sentry DSN, hidden input (optional)
GIT_SHA=$(git rev-parse --short HEAD) \
  docker compose --profile webhook --profile observability up -d --build webhook prometheus
```

**Logs.** With `LOG_FORMAT=json` (compose sets it) every line is one JSON object: `ts`, `level`, `logger`, `msg`, `exc`, `update_id`, `profile`. The update id is bound around each update, so every line a handler writes says which update it belongs to. Tokens, bearer tokens, API keys and passwords in URLs are masked in messages and tracebacks, and so are the exact values of the secrets the process holds: the generated ones have no shape a pattern could catch. uvicorn's access log is off; each update gets one line with its result and duration.

**Metrics.** `GET /metrics` needs `METRICS_TOKEN` as a bearer token and is off without it: the TLS proxy in front of the webhook forwards every path. Prometheus from the compose profile scrapes it on the internal network, and its UI is on 127.0.0.1:9090.

| metric | labels |
|---|---|
| `kit_updates_total` | `result`: processed, duplicate, in_progress, failed, rejected_secret, rejected_body; counts deliveries, so a retried one counts again; in_progress is a re-delivery answered 503 while the first attempt runs |
| `kit_update_duration_seconds` | histogram |
| `kit_bookings_total` | `result`: created, slot_taken, too_many, error |
| `kit_llm_requests_total` | `model`, `outcome`: ok, rate_limited, overloaded, bad_schema, timeout, error; timeout is a call cut off after 20 s, and the next model is tried |
| `kit_build_info` | `version`, `profile` |

Label values come from fixed sets: no user ids, chat ids or text. The server is a single uvicorn process, so `prometheus_client` needs no multiprocess mode. Long polling counts too but has no HTTP server, so only `serve` exposes the numbers.

**Sentry.** Off without `SENTRY_DSN`; nothing is sent and `sentry_sdk.init` is not called. With it, every ERROR line is an event tagged with `update_id` and `profile`, including the failures the webhook answers 200 for. Log messages go out as templates, without their arguments, in events and breadcrumbs alike: no user ids, and no objects a library logs. Events carry no request body, cookies, frame variables, secret header or Gemini prompts. The environment is `development` unless set, and compose sets `production`; the release is `tg-business-kit@<version>+<commit>` when the image is built with `GIT_SHA`. Why each of these would leak by default is in [ADR 7](docs/adr/0007-observability-without-leaks.md).

## How it is tested

- Updates travel the real python-telegram-bot `Application` against a fake Bot API; Gemini is faked; a guard fails any test that opens a connection outside localhost. No secrets are needed to run the suite.
- Every storage test runs twice, on the in-memory store and on Postgres, against one contract. CI starts Postgres and refuses to skip that half.
- Concurrency is exercised for real: five simultaneous saves of one slot let exactly one through, two clients confirming the same slot at once get one booking between them, and five simultaneous deliveries of one update start one attempt.
- A crash mid-handler is tested as what it leaves behind, a claim that was never marked done: the re-delivery past the window handles the update, and the one after that is a duplicate.
- The suite was checked by mutation: removing the update filter, the capacity check, the business timezone or the database lock each makes it fail.
- Observability is tested against leaks: secrets and a phone number go into log lines and Sentry events (through a test transport) and must not come out; every webhook outcome must move exactly one counter.

## Security notes

- The webhook secret is a header compared in constant time; an unset secret rejects everything.
- Secrets are never printed: settings errors name the missing keys, never values; scripts print statuses only; the bot token never appears on a command line or in logs.
- The bot's containers run as uid 10001 with a read-only filesystem and `no-new-privileges`. The webhook port is published on 127.0.0.1 only and the database is not published at all.
- Client messages are not logged, only their length.
- `/metrics` has its own token, separate from the owner's API token. Sentry events go out without request bodies, secret headers or frame variables, and Prometheus listens on 127.0.0.1 only.

## Design decisions

1. [One JSON profile per business](docs/adr/0001-one-profile-per-business.md)
2. [Booking state in callback_data](docs/adr/0002-booking-state-in-callback-data.md)
3. [The store owns atomicity](docs/adr/0003-the-store-owns-atomicity.md)
4. [An idempotent webhook](docs/adr/0004-idempotent-webhook.md)
5. [Plain SQL migrations](docs/adr/0005-plain-sql-migrations.md)
6. [Secrets are never printed](docs/adr/0006-secrets-never-printed.md)
7. [Observability without leaks](docs/adr/0007-observability-without-leaks.md)

## Environment

| key | needed for |
|---|---|
| `TELEGRAM_BOT_TOKEN` | always |
| `GEMINI_API_KEY` | `modules.ai_answers` |
| `ADMIN_USER_ID` | owner notifications, `/leads`, the owner's menu |
| `WEBHOOK_SECRET` | `serve`; generated by `scripts/setup_env.py` |
| `DATABASE_URL` | Postgres; set by compose; unset = in-memory store |
| `POSTGRES_PASSWORD` | the compose database; generated |
| `ADMIN_API_TOKEN` | `GET /api/leads`; generated; unset = endpoint off |
| `METRICS_TOKEN` | `GET /metrics` and the compose Prometheus; generated; unset = endpoint off |
| `SENTRY_DSN` | Sentry; `setup_env.py --sentry`; unset = Sentry off |
| `SENTRY_ENVIRONMENT` | Sentry; default `development`, compose sets `production` |
| `SENTRY_RELEASE` | Sentry; default `tg-business-kit@<version>+<GIT_SHA>` |
| `GIT_SHA` | build argument, not a `.env` key: `GIT_SHA=$(git rev-parse --short HEAD)` before `docker compose ... --build` |
| `LOG_FORMAT` | `text` (default) or `json`; compose sets `json` |

## Roadmap

- owner commands to confirm, cancel or move a booking
- reminders to clients the day before
- a product catalog with semantic search for shops

## License

[MIT](LICENSE)
