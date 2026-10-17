"""Bot API calls and settings for the scripts.

The token is part of every Bot API URL, so nothing here prints a URL, an
exception that could carry one, or a value from .env.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kit.settings import Settings, SettingsError, load_settings  # noqa: E402


def settings() -> Settings:
    try:
        return load_settings(ROOT / ".env")
    except SettingsError as e:
        sys.exit(f"[fail] {e}")


def call(token: str, method: str, params: dict | None = None, timeout: int = 15) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)  # Telegram explains the error in the body
        except ValueError:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        sys.exit(f"[fail] cannot reach Telegram: {e.reason}")
