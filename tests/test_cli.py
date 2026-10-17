import json

from kit.__main__ import main
from tests.conftest import ROOT

DENTAL = str(ROOT / "presets" / "dental-clinic.json")


def test_validate_good_profiles(capsys):
    assert main(["validate", *map(str, sorted((ROOT / "presets").glob("*.json")))]) == 0
    out = capsys.readouterr().out
    assert out.count("ok ") == 2


def test_validate_reports_the_problem_and_fails(tmp_path, capsys):
    raw = json.loads((ROOT / "presets" / "dental-clinic.json").read_text(encoding="utf-8"))
    raw["services"][1]["code"] = "chk"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(raw), encoding="utf-8")

    assert main(["validate", DENTAL, str(broken)]) == 1
    out = capsys.readouterr().out
    assert f"error  {broken}" in out and "duplicate code(s) chk" in out


def test_run_without_a_token_explains_what_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert main(["run", "--profile", DENTAL]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out


def test_run_needs_a_gemini_key_when_ai_answers_are_on(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["run", "--profile", DENTAL]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().out
