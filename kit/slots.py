"""Slot calendar built from the weekly template in the profile.

Pure functions: the current moment is always passed in, so the edge of the day
in the business timezone is testable. Booking starts from tomorrow in that
timezone; same-day booking is left to a human.
"""

from datetime import date, datetime, timedelta

from kit.profile import WEEKDAYS, Profile


def local_today(profile: Profile, now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(profile.tz).date()


def template_times(profile: Profile, day: date) -> list[str]:
    if profile.booking is None:
        return []
    return profile.booking.weekly_slots.get(WEEKDAYS[day.weekday()], [])


def upcoming_days(profile: Profile, now: datetime) -> list[date]:
    """Days from tomorrow on that have at least one slot in the template."""
    if profile.booking is None:
        return []
    today = local_today(profile, now)
    days = []
    for offset in range(1, profile.booking.days_ahead + 1):
        day = today + timedelta(days=offset)
        if template_times(profile, day):
            days.append(day)
        if len(days) >= profile.booking.max_day_buttons:
            break
    return days


def is_bookable(profile: Profile, day: date, time: str, now: datetime) -> bool:
    """A stale or forged button must not create a lead: the day has to be in
    the offered window and the time in that weekday's template."""
    return day in upcoming_days(profile, now) and time in template_times(profile, day)
