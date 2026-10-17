import importlib.util
import stat

import pytest

from tests.conftest import ROOT

TOKEN = "123456:SUPER-SECRET-TOKEN"
GEMINI = "AIza-very-secret"


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_env", ROOT / "scripts" / "setup_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ENV", tmp_path / ".env")
    monkeypatch.setattr(module, "bot_username", lambda token: "kit_demo_bot")
    answers = iter([TOKEN, GEMINI])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr("builtins.input", lambda prompt: "424242")
    return module


def test_env_is_written_merged_private_and_without_inline_comments(setup_env, capsys):
    setup_env.ENV.write_text("# keep me\nOTHER_KEY=stays\n", encoding="utf-8")
    setup_env.main()

    content = setup_env.ENV.read_text(encoding="utf-8")
    assert f"TELEGRAM_BOT_TOKEN={TOKEN}\n" in content
    assert "OTHER_KEY=stays" in content
    values = [line for line in content.splitlines() if not line.startswith("#")]
    assert all("#" not in line for line in values)  # comments only on their own line
    assert stat.S_IMODE(setup_env.ENV.stat().st_mode) == 0o600


def test_secrets_never_reach_the_terminal(setup_env, capsys):
    setup_env.main()
    out = capsys.readouterr().out
    assert TOKEN not in out and GEMINI not in out
    assert "TELEGRAM_BOT_TOKEN=set" in out and "GEMINI_API_KEY=set" in out


def test_rejected_token_stops_before_writing(setup_env, monkeypatch):
    monkeypatch.setattr(setup_env, "bot_username", lambda token: None)
    with pytest.raises(SystemExit, match="did not accept"):
        setup_env.main()
    assert not setup_env.ENV.exists()
