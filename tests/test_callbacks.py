from datetime import date

import pytest

from kit import callbacks
from kit.callbacks import BookingStep
from kit.profile import WEEKDAYS, load_profile
from tests.test_profile import PRESETS

DAY = date(2026, 10, 20)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (callbacks.booking("s", "chk"), BookingStep("s", "chk")),
        (callbacks.booking("d", "chk", DAY), BookingStep("d", "chk", DAY)),
        (callbacks.booking("t", "chk", DAY, "09:30"), BookingStep("t", "chk", DAY, "09:30")),
        (callbacks.booking("c", "chk", DAY, "18:00"), BookingStep("c", "chk", DAY, "18:00")),
    ],
)
def test_booking_round_trip(data, expected):
    assert callbacks.parse_booking(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        "",
        "b",
        "b:s",
        "b:s:BAD",
        "b:x:chk",
        "b:d:chk:2610xx",
        "b:d:chk:261340",
        "b:t:chk:261020:2500",
        "b:c:chk:261020",
        "b:c:chk:261020:0900:extra",
    ],
)
def test_malformed_booking_data_is_none(data):
    assert callbacks.parse_booking(data) is None


@pytest.mark.parametrize("data", ["f:", "f:x", "f:-1", "f:²", "f:1000", "m:2"])
def test_malformed_faq_data_is_none(data):
    assert callbacks.parse_faq(data) is None


def test_menu_screens_round_trip():
    for screen in callbacks.MENU_SCREENS:
        assert callbacks.parse_menu(callbacks.menu(screen)) == screen
    assert callbacks.parse_menu("m:admin") is None


@pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.name)
def test_longest_button_of_every_preset_fits_telegram_limit(path):
    profile = load_profile(path)
    longest = max(profile.services, key=lambda s: len(s.code)).code
    data = callbacks.booking("c", longest, date(2099, 12, 31), "23:59")
    assert len(data.encode()) <= callbacks.MAX_BYTES
    assert set(WEEKDAYS) >= set(profile.booking.weekly_slots)


def test_over_budget_payload_is_refused():
    with pytest.raises(ValueError, match="64 bytes"):
        callbacks._checked("x" * 65)
