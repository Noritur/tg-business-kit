"""What Telegram knows about this bot's webhook. Read-only.

    uv run python scripts/webhook_info.py
"""

from datetime import UTC, datetime

from _telegram import call, settings


def show(token: str) -> None:
    info = call(token, "getWebhookInfo")
    if not info.get("ok"):
        print(f"[fail] getWebhookInfo: {info.get('description')}")
        return
    result = info["result"]
    print(f"url:                  {result.get('url') or '(none - the bot uses polling)'}")
    print(f"pending_update_count: {result.get('pending_update_count', 0)}")
    if result.get("last_error_message"):
        when = datetime.fromtimestamp(result["last_error_date"], UTC).isoformat(timespec="seconds")
        print(f"last_error:           {result['last_error_message']} ({when})")
    else:
        print("last_error:           none")


if __name__ == "__main__":
    show(settings().telegram_bot_token.get_secret_value())
