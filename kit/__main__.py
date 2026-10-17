"""Command line.

    python -m kit validate <profile>...            check profile files
    python -m kit run --profile <profile>          long polling (local, a vps without a url)
    python -m kit serve --profile <profile>        webhook server (needs WEBHOOK_SECRET)
"""

import argparse
import logging
import sys

from kit.profile import ProfileError, load_profile

log = logging.getLogger("kit")


def _validate(paths: list[str]) -> int:
    failed = 0
    for path in paths:
        try:
            p = load_profile(path)
        except ProfileError as e:
            failed += 1
            print(f"error  {path}")
            for problem in e.problems:
                print(f"       {problem}")
            continue
        on = [name for name, enabled in p.modules.model_dump().items() if enabled]
        print(
            f"ok     {path}  {p.name} ({p.language}), {len(p.services)} services, "
            f"{len(p.faq)} faq, modules: {', '.join(on) or 'none'}"
        )
    return 1 if failed else 0


class _SetupError(Exception):
    """A startup problem with a message meant for the person running the bot."""


def _setup(profile_path: str):
    """Profile + env + dependencies, or _SetupError with what to fix."""
    from kit.app import Deps
    from kit.llm import GeminiAnswerer
    from kit.settings import SettingsError, load_settings
    from kit.storage import MemoryLeadStore

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx logs every Bot API URL, and those URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        raise _SetupError("\n  ".join([f"profile {e.path} is not valid:", *e.problems])) from None
    try:
        settings = load_settings()
    except SettingsError as e:
        raise _SetupError(str(e)) from None

    answerer = None
    if profile.modules.ai_answers:
        if settings.gemini_api_key is None:
            raise _SetupError("GEMINI_API_KEY is required when modules.ai_answers is on")
        answerer = GeminiAnswerer(settings.gemini_api_key.get_secret_value())
    if not settings.admin_user_id:
        log.warning("ADMIN_USER_ID not set: new leads and open questions reach no one")
    log.warning("leads are kept in memory and vanish on restart (Postgres comes in phase 3)")

    deps = Deps(
        profile=profile, store=MemoryLeadStore(), answerer=answerer, admin_id=settings.admin_user_id
    )
    return deps, settings


def _run(profile_path: str) -> int:
    from kit.app import build_app

    deps, settings = _setup(profile_path)
    app = build_app(deps, settings.telegram_bot_token.get_secret_value())
    log.info("polling as %s (%s)", deps.profile.name, deps.profile.slug)
    app.run_polling()
    return 0


def _serve(profile_path: str, host: str, port: int) -> int:
    import uvicorn

    from kit.web import create_app

    deps, settings = _setup(profile_path)
    if settings.webhook_secret is None:
        raise _SetupError("WEBHOOK_SECRET is required for serve (scripts/setup_env.py makes one)")
    api = create_app(
        deps,
        settings.telegram_bot_token.get_secret_value(),
        settings.webhook_secret.get_secret_value(),
    )
    log.info("webhook server for %s on %s:%s", deps.profile.slug, host, port)
    uvicorn.run(api, host=host, port=port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check one or more profile files")
    validate.add_argument("paths", nargs="+")
    run = sub.add_parser("run", help="run the bot with long polling")
    run.add_argument("--profile", required=True)
    serve = sub.add_parser("serve", help="run the webhook server")
    serve.add_argument("--profile", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate(args.paths)
    try:
        if args.command == "run":
            return _run(args.profile)
        return _serve(args.profile, args.host, args.port)
    except _SetupError as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
