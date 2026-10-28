import importlib.util
import stat

import pytest

from tests.conftest import ROOT

TOKEN = "123456:SUPER-SECRET-TOKEN"
GEMINI = "AIza-very-secret"


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))  # for its `import _telegram`
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


def test_webhook_secret_is_generated_once_and_never_printed(setup_env, capsys, monkeypatch):
    setup_env.main()
    first = setup_env.read_env()["WEBHOOK_SECRET"]
    assert len(first) >= 40 and first not in capsys.readouterr().out

    answers = iter([TOKEN, GEMINI])
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda prompt: next(answers))
    setup_env.main()
    assert setup_env.read_env()["WEBHOOK_SECRET"] == first  # a rerun keeps the secret


def test_a_username_instead_of_the_id_is_asked_again(setup_env, monkeypatch, capsys):
    typed = iter(["@some_username", "424242"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(typed))
    setup_env.main()
    assert setup_env.read_env()["ADMIN_USER_ID"] == "424242"
    assert "not a @username" in capsys.readouterr().out


def test_generated_only_adds_missing_secrets_without_asking(setup_env, monkeypatch, capsys):
    setup_env.ENV.write_text("TELEGRAM_BOT_TOKEN=keep-me\nWEBHOOK_SECRET=already-here\n")
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda prompt: pytest.fail("asked"))
    monkeypatch.setattr(setup_env.sys, "argv", ["setup_env.py", "--generated-only"])
    setup_env.main()

    values = setup_env.read_env()
    assert values["TELEGRAM_BOT_TOKEN"] == "keep-me" and values["WEBHOOK_SECRET"] == "already-here"
    out = capsys.readouterr().out
    assert "POSTGRES_PASSWORD, ADMIN_API_TOKEN" in out
    assert values["POSTGRES_PASSWORD"] not in out and values["ADMIN_API_TOKEN"] not in out


def test_generated_only_also_makes_the_metrics_token(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(setup_env.sys, "argv", ["setup_env.py", "--generated-only"])
    setup_env.main()
    token = setup_env.read_env()["METRICS_TOKEN"]
    assert len(token) >= 40 and token not in capsys.readouterr().out


DSN = "https://" + "ab12" * 8 + "@o4501.ingest.de.sentry.io/4502"


def test_sentry_dsn_is_written_and_never_printed(setup_env, monkeypatch, capsys):
    setup_env.ENV.write_text("TELEGRAM_BOT_TOKEN=keep-me\n")
    typed = iter(["not-a-dsn", TOKEN, DSN])  # a mistake, another secret pasted, then the DSN
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda prompt: next(typed))
    monkeypatch.setattr(setup_env.sys, "argv", ["setup_env.py", "--sentry"])
    setup_env.main()

    values = setup_env.read_env()
    assert values["SENTRY_DSN"] == DSN and values["TELEGRAM_BOT_TOKEN"] == "keep-me"
    out = capsys.readouterr().out
    assert out.count("not a DSN") == 2 and "SENTRY_DSN=set" in out
    assert DSN not in out and TOKEN not in out and "not-a-dsn" not in out
    assert stat.S_IMODE(setup_env.ENV.stat().st_mode) == 0o600


def test_sentry_with_nothing_typed_changes_nothing(setup_env, monkeypatch):
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr(setup_env.sys, "argv", ["setup_env.py", "--sentry"])
    setup_env.main()
    assert not setup_env.ENV.exists()
