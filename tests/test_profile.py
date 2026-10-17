import json

import pytest

from kit.profile import ProfileError, load_profile
from tests.conftest import ROOT

PRESETS = sorted((ROOT / "presets").glob("*.json"))


@pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.name)
def test_every_preset_is_valid(path):
    profile = load_profile(path)
    assert profile.services and profile.faq


def _problems(tmp_path, raw) -> list[str]:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(raw) if isinstance(raw, dict) else raw, encoding="utf-8")
    with pytest.raises(ProfileError) as caught:
        load_profile(path)
    return caught.value.problems


def _base() -> dict:
    return json.loads((ROOT / "presets" / "dental-clinic.json").read_text(encoding="utf-8"))


BROKEN = {
    "duplicate service code": (
        lambda r: r["services"][1].update(code="chk"),
        "duplicate code(s) chk",
    ),
    "service code with capitals": (
        lambda r: r["services"][0].update(code="Check-Up"),
        "services.0.code",
    ),
    "impossible time": (
        lambda r: r["booking"]["weekly_slots"].update(mon=["25:00"]),
        "'25:00' is not a HH:MM time",
    ),
    "times out of order": (
        lambda r: r["booking"]["weekly_slots"].update(mon=["10:00", "09:00"]),
        "ascending order",
    ),
    "unknown weekday": (
        lambda r: r["booking"]["weekly_slots"].update(monday=["09:00"]),
        "unknown weekday 'monday'",
    ),
    "unknown timezone": (lambda r: r.update(timezone="Mars/Base"), "unknown timezone"),
    "booking without services": (lambda r: r.update(services=[]), "services is empty"),
    "faq module without faq": (lambda r: r.update(faq=[]), "faq is empty"),
    "unknown text key": (lambda r: r.update(texts={"greting": "Hi"}), "texts.greting"),
    "typo in a placeholder": (
        lambda r: r.update(texts={"booked": "Done: {servce}"}),
        "unknown placeholder(s) servce",
    ),
    "unknown field": (lambda r: r.update(colour="blue"), "colour"),
}


@pytest.mark.parametrize("case", BROKEN, ids=str)
def test_broken_profile_names_the_problem(tmp_path, case):
    mutate, expected = BROKEN[case]
    raw = _base()
    mutate(raw)
    problems = _problems(tmp_path, raw)
    assert any(expected in p for p in problems), problems


def test_invalid_json_is_reported_with_a_line(tmp_path):
    [problem] = _problems(tmp_path, '{"slug": "x",\n')
    assert "not valid JSON" in problem and "line" in problem


def test_missing_file(tmp_path):
    with pytest.raises(ProfileError) as caught:
        load_profile(tmp_path / "nope.json")
    assert caught.value.problems == ["file not found"]


def test_disabled_booking_needs_no_slots(tmp_path):
    raw = _base()
    raw["modules"]["booking"] = False
    raw["services"] = []
    del raw["booking"]
    path = tmp_path / "faq-only.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_profile(path).booking is None
