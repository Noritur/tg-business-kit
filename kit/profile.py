"""Business profile: everything client-specific lives in one JSON file.

Setting the engine up for a new business means writing a profile and running
`python -m kit validate <file>`. Validation is strict on purpose: an unknown
field, a duplicate service code or a broken time is reported with its path
before the bot ever starts.
"""

import json
import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kit.texts import Texts, check_overrides

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ProfileError(Exception):
    """A profile that cannot be used; `problems` holds one readable line each."""

    def __init__(self, path: str, problems: list[str]):
        self.path = path
        self.problems = problems
        super().__init__(f"{path}: " + "; ".join(problems))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Service(_Strict):
    # Short codes keep callback_data far below Telegram's 64-byte limit.
    code: str = Field(pattern=r"^[a-z0-9]{1,6}$")
    label: str = Field(min_length=1, max_length=60)
    price: str = Field(min_length=1)
    note: str = ""


class Trial(_Strict):
    enabled: bool = False
    label: str = ""


class Booking(_Strict):
    weekly_slots: dict[str, list[str]]
    days_ahead: int = Field(default=10, ge=1, le=60)
    max_day_buttons: int = Field(default=6, ge=1, le=14)
    slot_capacity: int = Field(default=1, ge=1, le=100)
    # Upcoming bookings one client may hold: one person must not fill the calendar.
    max_per_client: int = Field(default=2, ge=1, le=20)
    trial: Trial = Field(default_factory=Trial)

    @field_validator("weekly_slots")
    @classmethod
    def _check_slots(cls, slots: dict[str, list[str]]) -> dict[str, list[str]]:
        for day, times in slots.items():
            if day not in WEEKDAYS:
                raise ValueError(f"unknown weekday {day!r}, use one of: {', '.join(WEEKDAYS)}")
            for t in times:
                if not _TIME_RE.match(t):
                    raise ValueError(f"{day}: {t!r} is not a HH:MM time")
            if times != sorted(set(times)):
                raise ValueError(f"{day}: times must be unique and in ascending order")
        return slots


class FaqItem(_Strict):
    q: str = Field(min_length=1, max_length=64)  # shown as a button label
    a: str = Field(min_length=1)


class Modules(_Strict):
    booking: bool = True
    faq: bool = True
    ai_answers: bool = True


class Profile(_Strict):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    name: str = Field(min_length=1)
    tagline: str = ""
    about: str = ""
    language: Literal["uk", "en"]
    timezone: str
    owner_contact: str = Field(min_length=1)
    modules: Modules = Field(default_factory=Modules)
    services: list[Service] = Field(default_factory=list)
    booking: Booking | None = None
    faq: list[FaqItem] = Field(default_factory=list)
    texts: dict[str, str] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, tz: str) -> str:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown timezone {tz!r}, e.g. Europe/Madrid") from None
        return tz

    @model_validator(mode="after")
    def _check_consistency(self) -> "Profile":
        problems = []
        codes = [s.code for s in self.services]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            problems.append(f"services: duplicate code(s) {', '.join(dupes)}")
        if self.modules.booking:
            if not self.services:
                problems.append("modules.booking is on, but services is empty")
            if self.booking is None or not any(self.booking.weekly_slots.values()):
                problems.append("modules.booking is on, but booking.weekly_slots has no times")
        if self.modules.faq and not self.faq:
            problems.append("modules.faq is on, but faq is empty")
        problems += check_overrides(self.language, self.texts)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def city(self) -> str:
        return self.timezone.rsplit("/", 1)[-1].replace("_", " ")

    @property
    def t(self) -> Texts:
        return Texts(self.language, self.texts)

    def service(self, code: str) -> Service | None:
        return next((s for s in self.services if s.code == code), None)


def _readable(error: ValidationError) -> list[str]:
    lines = []
    for err in error.errors():
        where = ".".join(str(part) for part in err["loc"]) or "profile"
        message = err["msg"].removeprefix("Value error, ")
        lines.append(f"{where}: {message}")
    return lines


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(str(path), ["file not found"]) from None
    except json.JSONDecodeError as e:
        raise ProfileError(str(path), [f"not valid JSON: {e.msg} (line {e.lineno})"]) from None
    try:
        return Profile.model_validate(raw)
    except ValidationError as e:
        raise ProfileError(str(path), _readable(e)) from None
