"""Writes .env for a local run. Secrets go in through hidden prompts and are
never printed, logged or passed on a command line.

    uv run python scripts/setup_env.py

Steps: token (checked with getMe) -> Gemini key (optional) -> owner id ->
.env with permissions 600. Other keys already in .env are kept; comments are
rewritten from COMMENTS, a hand-written comment is not kept.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"
COMMENTS = {
    "TELEGRAM_BOT_TOKEN": "# Telegram bot token from @BotFather",
    "GEMINI_API_KEY": "# Gemini API key; empty = no AI answers",
    "ADMIN_USER_ID": "# Owner's Telegram user id for notifications and /leads",
}


def bot_username(token: str) -> str | None:
    """The bot's @username if Telegram accepts the token, None if it does not."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
    except urllib.error.HTTPError:
        return None
    except urllib.error.URLError as e:
        # The reason never contains the URL, so the token cannot leak here.
        sys.exit(f"[fail] cannot reach Telegram: {e.reason}")
    return data["result"].get("username") if data.get("ok") else None


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
    admin_id = input("id: ").strip()
    if admin_id and not (admin_id.isascii() and admin_id.isdigit()):
        sys.exit("[fail] the user id is a number, e.g. 123456789")

    values = read_env()
    values.update(
        {"TELEGRAM_BOT_TOKEN": token, "GEMINI_API_KEY": gemini_key, "ADMIN_USER_ID": admin_id}
    )
    write_env(values)
    statuses = ", ".join(f"{key}={'set' if values[key] else 'empty'}" for key in COMMENTS)
    print(f"[4/4] {ENV.name} written with permissions 600: {statuses}")


if __name__ == "__main__":
    main()
