# tg-business-kit

One Telegram bot engine for small service businesses — language schools, dental clinics, beauty studios, tutors. Every client is one JSON profile: services, weekly slots, FAQ and UI texts. The engine turns it into a button-driven booking flow, an FAQ menu and AI answers grounded strictly in the profile.

Status: phase 1 of 4 — the engine runs locally with long polling and an in-memory lead store. Webhook deploy, Postgres and CI come next.

## Try it

```bash
uv sync
uv run python -m kit validate presets/*.json
uv run pytest
```

Run a bot against your own test token (create one with @BotFather):

```bash
uv run python scripts/setup_env.py
uv run python -m kit run --profile presets/dental-clinic.json
```

## Presets

| preset | business | language |
|---|---|---|
| `presets/language-school.json` | Lingua Amiga, online Spanish school (demo) | uk |
| `presets/dental-clinic.json` | Bright Smile Dental (demo) | en |

All businesses in the presets are fictional.
