"""Writes .env for a local run. Secrets go in through hidden prompts and are
never printed, logged or passed on a command line.

    uv run python scripts/setup_env.py

Steps: token (checked with getMe) -> Gemini key (optional) -> owner id ->
WEBHOOK_SECRET generated if missing -> .env with permissions 600. Other keys
already in .env are kept; comments are rewritten from COMMENTS, a hand-written
comment is not kept.
"""

import getpass
import os
import secrets
import sys

from _telegram import ROOT, call

ENV = ROOT / ".env"
COMMENTS = {
    "TELEGRAM_BOT_TOKEN": "# Telegram bot token from @BotFather",
    "GEMINI_API_KEY": "# Gemini API key; empty = no AI answers",
    "ADMIN_USER_ID": "# Owner's Telegram user id for notifications and /leads",
    "WEBHOOK_SECRET": "# Shared secret Telegram sends with every webhook call",
}


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


def main() -> None:
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
    if not values.get("WEBHOOK_SECRET"):
        # Telegram allows A-Z, a-z, 0-9, _ and -, up to 256 characters.
        values["WEBHOOK_SECRET"] = secrets.token_urlsafe(32)
    write_env(values)
    statuses = ", ".join(f"{key}={'set' if values[key] else 'empty'}" for key in COMMENTS)
    print(f"[4/4] {ENV.name} written with permissions 600: {statuses}")


if __name__ == "__main__":
    main()
