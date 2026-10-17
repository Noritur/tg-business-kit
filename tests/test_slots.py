from datetime import UTC, date, datetime

import pytest

from kit.slots import is_bookable, local_today, upcoming_days
from tests.conftest import MONDAY_NOON

TUESDAY = date(2026, 10, 20)
SUNDAY = date(2026, 10, 25)


def test_days_start_tomorrow_skip_empty_weekdays_and_stop_at_the_button_limit(dental):
    days = upcoming_days(dental, MONDAY_NOON)
    assert days[0] == TUESDAY
    assert SUNDAY not in days  # the clinic is closed on Sunday
    assert len(days) == dental.booking.max_day_buttons


def test_day_boundary_follows_the_business_timezone(school):
    # 22:30 UTC on Sunday is already 00:30 on Monday in Madrid.
    late_sunday_utc = datetime(2026, 10, 18, 22, 30, tzinfo=UTC)
    assert local_today(school, late_sunday_utc) == date(2026, 10, 19)
    assert upcoming_days(school, late_sunday_utc)[0] == TUESDAY


def test_naive_time_is_refused(dental):
    with pytest.raises(ValueError, match="timezone-aware"):
        upcoming_days(dental, datetime(2026, 10, 19, 12, 0))


@pytest.mark.parametrize(
    ("day", "time", "expected"),
    [
        (TUESDAY, "09:00", True),
        (date(2026, 10, 19), "09:00", False),  # today: same-day booking is off
        (date(2026, 10, 18), "09:00", False),  # the past
        (SUNDAY, "10:00", False),  # no slots that weekday
        (date(2026, 11, 30), "09:00", False),  # beyond the offered window
        (TUESDAY, "09:30", False),  # not in the template
    ],
)
def test_is_bookable(dental, day, time, expected):
    assert is_bookable(dental, day, time, MONDAY_NOON) is expected
