"""callback_data grammar for inline buttons.

The whole booking flow is encoded in callback_data, so a step survives a
restart or a serverless invocation without server-side sessions. Telegram caps
callback_data at 64 bytes; every builder checks the budget.

  m:<screen>                  menu: home | prices | sched | faq
  f:<index>                   faq item
  b:svc                       booking entry: pick a service
  b:s:<svc>                   service picked  -> pick a day
  b:d:<svc>:<YYMMDD>          day picked      -> pick a time
  b:t:<svc>:<YYMMDD>:<HHMM>   time picked     -> confirm screen
  b:c:<svc>:<YYMMDD>:<HHMM>   confirmed       -> save the lead

Parsers return None for anything malformed: a button can arrive stale, from
an older version of the bot, or forged by a modified client.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

MAX_BYTES = 64
MENU_SCREENS = ("home", "prices", "sched", "faq")

_SERVICE_RE = re.compile(r"^[a-z0-9]{1,6}$")
_TIME_CODE_RE = re.compile(r"^([01]\d|2[0-3])[0-5]\d$")


def _checked(data: str) -> str:
    if len(data.encode()) > MAX_BYTES:
        raise ValueError(f"callback_data over {MAX_BYTES} bytes: {data!r}")
    return data


def menu(screen: str) -> str:
    if screen not in MENU_SCREENS:
        raise ValueError(f"unknown menu screen {screen!r}")
    return _checked(f"m:{screen}")


def faq(index: int) -> str:
    return _checked(f"f:{index}")


def booking_start() -> str:
    return "b:svc"


@dataclass(frozen=True)
class BookingStep:
    step: str  # "s" | "d" | "t" | "c"
    service: str
    day: date | None = None
    time: str | None = None  # "HH:MM"


def booking(step: str, service: str, day: date | None = None, time: str | None = None) -> str:
    parts = ["b", step, service]
    if step in ("d", "t", "c"):
        parts.append(f"{day:%y%m%d}")
    if step in ("t", "c"):
        parts.append(time.replace(":", ""))
    return _checked(":".join(parts))


def parse_menu(data: str) -> str | None:
    prefix, _, screen = data.partition(":")
    return screen if prefix == "m" and screen in MENU_SCREENS else None


def parse_faq(data: str) -> int | None:
    prefix, _, index = data.partition(":")
    # str.isdigit() accepts "²", which int() rejects: match ASCII digits only.
    return int(index) if prefix == "f" and re.fullmatch(r"[0-9]{1,3}", index) else None


def parse_booking(data: str) -> BookingStep | None:
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "b":
        return None
    step, service = parts[1], parts[2]
    if not _SERVICE_RE.match(service):
        return None
    expected = {"s": 3, "d": 4, "t": 5, "c": 5}
    if expected.get(step) != len(parts):
        return None
    day = time = None
    if step in ("d", "t", "c"):
        try:
            day = datetime.strptime(parts[3], "%y%m%d").date()
        except ValueError:
            return None
    if step in ("t", "c"):
        if not _TIME_CODE_RE.match(parts[4]):
            return None
        time = f"{parts[4][:2]}:{parts[4][2:]}"
    return BookingStep(step=step, service=service, day=day, time=time)
