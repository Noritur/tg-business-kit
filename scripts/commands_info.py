"""What the "Menu" button shows right now, for clients and for the owner. Read-only.

    uv run python scripts/commands_info.py
"""

import json

from _telegram import call, settings


def show(token: str, admin_id: int) -> None:
    scopes = [("clients", None)]
    if admin_id:
        scopes.append(("owner", json.dumps({"type": "chat", "chat_id": admin_id})))
    for who, scope in scopes:
        result = call(token, "getMyCommands", {"scope": scope} if scope else None)
        if not result.get("ok"):
            print(f"{who:8} [fail] {result.get('description')}")
            continue
        commands = " ".join(f"/{c['command']}" for c in result["result"]) or "(empty)"
        print(f"{who:8} {commands}")


if __name__ == "__main__":
    s = settings()
    show(s.telegram_bot_token.get_secret_value(), s.admin_user_id)
