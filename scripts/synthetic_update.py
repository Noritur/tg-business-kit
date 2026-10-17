"""POSTs a synthetic text update to a webhook server, as Telegram would.

    uv run python scripts/synthetic_update.py http://localhost:8000/telegram/webhook "hi"

The message comes "from" ADMIN_USER_ID, so the bot's reply lands in your own
chat with the bot. Pass --no-secret to check that the server answers 403.
"""

import json
import sys
import time
import urllib.error
import urllib.request

from _telegram import settings


def main(argv: list[str]) -> None:
    with_secret = "--no-secret" not in argv
    args = [a for a in argv if a != "--no-secret"]
    if not args:
        sys.exit(__doc__)
    url, text = args[0], " ".join(args[1:]) or "hi"
    s = settings()
    if not s.admin_user_id:
        sys.exit("[fail] ADMIN_USER_ID is not set: the reply would have nowhere to go")
    if with_secret and s.webhook_secret is None:
        sys.exit("[fail] WEBHOOK_SECRET is not set: run scripts/setup_env.py")

    now = int(time.time())
    update = {
        "update_id": now,
        "message": {
            "message_id": now % 100000,
            "date": now,
            "chat": {"id": s.admin_user_id, "type": "private"},
            "from": {"id": s.admin_user_id, "is_bot": False, "first_name": "Synthetic"},
            "text": text,
        },
    }
    headers = {"Content-Type": "application/json"}
    if with_secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = s.webhook_secret.get_secret_value()
    request = urllib.request.Request(url, data=json.dumps(update).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"[ok]  {response.status} {response.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"[http] {e.code} {e.read().decode()}")
    except urllib.error.URLError as e:
        sys.exit(f"[fail] cannot reach {url}: {e.reason}")


if __name__ == "__main__":
    main(sys.argv[1:])
