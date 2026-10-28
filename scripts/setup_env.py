"""Writes .env for a local run. Secrets go in through hidden prompts and are
never printed, logged or passed on a command line.

    uv run python scripts/setup_env.py                   ask for token, key and owner id
    uv run python scripts/setup_env.py --generated-only  only add missing generated secrets
    uv run python scripts/setup_env.py --sentry          only ask for the Sentry DSN

Steps: token (checked with getMe) -> Gemini key (optional) -> owner id ->
generated secrets added if missing -> .env with permissions 600. Other keys
already in .env are kept; comments are rewritten from COMMENTS, a hand-written
comment is not kept.
"""

import getpass
import os
import re
import secrets
import sys

from _telegram import ROOT, call

ENV = ROOT / ".env"
COMMENTS = {
    "TELEGRAM_BOT_TOKEN": "# Telegram bot token from @BotFather",
    "GEMINI_API_KEY": "# Gemini API key; empty = no AI answers",
    "ADMIN_USER_ID": "# Owner's Telegram user id for notifications and /leads",
    "WEBHOOK_SECRET": "# Shared secret Telegram sends with every webhook call",
    "POSTGRES_PASSWORD": "# Password of the compose Postgres (docker compose reads it)",
    "ADMIN_API_TOKEN": "# Bearer token for GET /api/leads; empty = endpoint off",
    "METRICS_TOKEN": "# Bearer token Prometheus sends to GET /metrics; empty = endpoint off",
    "SENTRY_DSN": "# Sentry DSN; empty = Sentry off, nothing is sent",
}
# Made here, never typed by a person. token_urlsafe gives A-Z a-z 0-9 _ -, which
# Telegram accepts as a webhook secret and needs no escaping inside a URL.
GENERATED = ("WEBHOOK_SECRET", "POSTGRES_PASSWORD", "ADMIN_API_TOKEN", "METRICS_TOKEN")
# https://<32 hex public key>@<host>/<project id>; self-hosted Sentry may add a path.
DSN_RE = re.compile(r"^https?://[0-9a-f]{32}@[^/\s]+/(?:[^\s]+/)?\d+$")


def bot_username(token: str) -> str | None:
    """The bot's @username if Telegram accepts the token, None if it does not."""
    result = call(token, "getMe", timeout=10)
    return result["result"].get("username") if result.get("ok") else None


def read_env() -> dict[str, str]:
    if not ENV.exists():
        return {}
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    lines = []
    for key, value in values.items():
        if key in COMMENTS:
            lines.append(COMMENTS[key])  # comments on their own line, never inline
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV, 0o600)


def add_generated(values: dict[str, str]) -> list[str]:
    added = [key for key in GENERATED if not values.get(key)]
    for key in added:
        values[key] = secrets.token_urlsafe(32)
    return added


def generated_only() -> None:
    values = read_env()
    added = add_generated(values)
    if added:
        write_env(values)
    print(f"[ok]  generated: {', '.join(added) or 'nothing, all present'}")


def sentry() -> None:
    print("Sentry DSN: project settings -> Client Keys (input is hidden; Enter to cancel)")
    while True:
        dsn = getpass.getpass("dsn: ").strip()
        if not dsn:
            print("[ok]  nothing changed")
            return
        if DSN_RE.match(dsn):
            break
        # Never echo what was typed: it may be some other secret pasted by mistake.
        print("[!]   that is not a DSN (https://<key>@<host>/<project id>) - try again")
    values = read_env()
    values["SENTRY_DSN"] = dsn
    write_env(values)
    print(f"[ok]  {ENV.name} written with permissions 600: SENTRY_DSN=set")


def main() -> None:
    if "--generated-only" in sys.argv[1:]:
        generated_only()
        return
    if "--sentry" in sys.argv[1:]:
        sentry()
        return
    print("[1/4] Telegram bot token from @BotFather (input is hidden)")
    token = getpass.getpass("token: ").strip()
    username = bot_username(token) if token else None
    if username is None:
        sys.exit("[fail] Telegram did not accept this token")
    print(f"[ok]  the token belongs to @{username}")

    print("[2/4] Gemini API key (hidden; press Enter to skip, AI answers then stay off)")
    gemini_key = getpass.getpass("key: ").strip()

    print("[3/4] your Telegram user id for owner notifications (@userinfobot shows it)")
    while True:
        # Ask again instead of exiting: the token and key typed above would be lost.
        admin_id = input("id: ").strip()
        if not admin_id or (admin_id.isascii() and admin_id.isdigit()):
            break
        print("[!]   the id is a number like 123456789, not a @username - try again")

    values = read_env()
    values.update(
        {"TELEGRAM_BOT_TOKEN": token, "GEMINI_API_KEY": gemini_key, "ADMIN_USER_ID": admin_id}
    )
    add_generated(values)
    write_env(values)
    statuses = ", ".join(f"{key}={'set' if values.get(key) else 'empty'}" for key in COMMENTS)
    print(f"[4/4] {ENV.name} written with permissions 600: {statuses}")


if __name__ == "__main__":
    main()
