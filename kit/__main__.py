"""Command line: python -m kit validate <profile>... | run --profile <profile>"""

import argparse
import logging
import sys

from pydantic import ValidationError

from kit.profile import ProfileError, load_profile


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


def _run(profile_path: str) -> int:
    from kit.app import Deps, build_app
    from kit.llm import GeminiAnswerer
    from kit.settings import Settings
    from kit.storage import MemoryLeadStore

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx logs every Bot API URL, and those URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("kit")

    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        print(f"profile {e.path} is not valid:", *e.problems, sep="\n  ")
        return 2
    try:
        settings = Settings()
    except ValidationError as e:
        names = ", ".join(str(err["loc"][0]).upper() for err in e.errors())
        print(f"missing or invalid env: {names} (run scripts/setup_env.py or see .env.example)")
        return 2

    answerer = None
    if profile.modules.ai_answers:
        if settings.gemini_api_key is None:
            print("GEMINI_API_KEY is required when modules.ai_answers is on")
            return 2
        answerer = GeminiAnswerer(settings.gemini_api_key.get_secret_value())
    if not settings.admin_user_id:
        log.warning("ADMIN_USER_ID not set: new leads and open questions reach no one")
    log.warning("leads are kept in memory and vanish on restart (Postgres comes in phase 3)")

    deps = Deps(
        profile=profile,
        store=MemoryLeadStore(),
        answerer=answerer,
        admin_id=settings.admin_user_id,
    )
    app = build_app(deps, settings.telegram_bot_token.get_secret_value())
    log.info("polling as %s (%s)", profile.name, profile.slug)
    app.run_polling()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kit")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="check one or more profile files")
    validate.add_argument("paths", nargs="+")
    run = sub.add_parser("run", help="run the bot with long polling")
    run.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.paths)
    return _run(args.profile)


if __name__ == "__main__":
    sys.exit(main())
