"""Reads GET /api/leads with the token from .env, so it never sits on a command line.

    uv run python scripts/api_leads.py http://127.0.0.1:8000
    uv run python scripts/api_leads.py http://127.0.0.1:8000 --no-token   # expect 401
"""

import json
import sys
import urllib.error
import urllib.request

from _telegram import settings


def main(argv: list[str]) -> None:
    with_token = "--no-token" not in argv
    args = [a for a in argv if a != "--no-token"]
    if len(args) != 1:
        sys.exit(__doc__)
    url = args[0].rstrip("/") + "/api/leads?limit=10"
    headers = {}
    if with_token:
        token = settings().admin_api_token
        if token is None:
            sys.exit("[fail] ADMIN_API_TOKEN is not set: run scripts/setup_env.py --generated-only")
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            page = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[http] {e.code} {e.read().decode()}")
    except urllib.error.URLError as e:
        sys.exit(f"[fail] cannot reach {url}: {e.reason}")
    print(f"[ok]  {len(page['leads'])} lead(s), next_before_id={page['next_before_id']}")
    for lead in page["leads"]:
        who = f"@{lead['tg_username']}" if lead["tg_username"] else lead["tg_name"]
        print(f"  #{lead['id']} {lead['slot_date']} {lead['slot_time']} {lead['service_label']}"
              f" - {who} [{lead['status']}]")  # fmt: skip


if __name__ == "__main__":
    main(sys.argv[1:])
