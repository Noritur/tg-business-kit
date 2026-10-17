"""Points Telegram at the webhook server, then shows what Telegram reports.

    uv run python scripts/set_webhook.py https://<host>/telegram/webhook

Reads TELEGRAM_BOT_TOKEN and WEBHOOK_SECRET from .env. While a webhook is set,
long polling (python -m kit run) does not work: Telegram answers 409.
"""

import json
import sys

from _telegram import call, settings
from webhook_info import show


def main(argv: list[str]) -> None:
    if len(argv) != 1 or not argv[0].startswith("https://"):
        sys.exit(__doc__)
    s = settings()
    if s.webhook_secret is None:
        sys.exit("[fail] WEBHOOK_SECRET is not set: run scripts/setup_env.py")
    token = s.telegram_bot_token.get_secret_value()
    result = call(
        token,
        "setWebhook",
        {
            "url": argv[0],
            "secret_token": s.webhook_secret.get_secret_value(),
            "allowed_updates": json.dumps(["message", "callback_query"]),
        },
    )
    if result.get("ok"):
        print("[ok]  webhook set")
    else:
        print(f"[fail] setWebhook: {result.get('description')}")
    show(token)


if __name__ == "__main__":
    main(sys.argv[1:])
