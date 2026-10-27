"""Command line.

    python -m kit validate <profile>...            check profile files
    python -m kit run --profile <profile>          long polling (local, a vps without a url)
    python -m kit serve --profile <profile>        webhook server (needs WEBHOOK_SECRET)
    python -m kit migrate                          apply migrations/ to DATABASE_URL
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


def _settings(model=None):
    from kit.settings import Settings, SettingsError, load_settings

    try:
        return load_settings(model=model or Settings)
    except SettingsError as e:
        raise _SetupError(str(e)) from None


def _setup(profile_path: str):
    """Profile + env + dependencies, or _SetupError with what to fix.
    Returns (deps, settings, pool); pool is None for the in-memory store."""
    from kit import logs
    from kit.app import Deps
    from kit.llm import GeminiAnswerer
    from kit.storage import MemoryLeadStore, MemoryUpdateLog

    try:
        profile = load_profile(profile_path)
    except ProfileError as e:
        raise _SetupError("\n  ".join([f"profile {e.path} is not valid:", *e.problems])) from None
    settings = _settings()
    if profile.modules.ai_answers and settings.gemini_api_key is None:
        raise _SetupError("GEMINI_API_KEY is required when modules.ai_answers is on")
    # After settings: LOG_FORMAT lives there, and setup errors are printed, not logged.
    logs.setup(settings.log_format, profile=profile.slug)

    answerer = None
    if profile.modules.ai_answers:
        answerer = GeminiAnswerer(settings.gemini_api_key.get_secret_value())
    if not settings.admin_user_id:
        log.warning("ADMIN_USER_ID not set: new leads and open questions reach no one")

    pool = None
    if settings.database_url is not None:
        from kit.db import make_pool
        from kit.storage import PostgresLeadStore, PostgresUpdateLog

        pool = make_pool(settings.database_url.get_secret_value())
        store, updates = PostgresLeadStore(pool), PostgresUpdateLog(pool)
        log.info("store: postgres")
    else:
        store, updates = MemoryLeadStore(), MemoryUpdateLog()
        log.warning("store: memory - leads vanish on restart (set DATABASE_URL for Postgres)")

    deps = Deps(
        profile=profile,
        store=store,
        answerer=answerer,
        admin_id=settings.admin_user_id,
        updates=updates,
    )
    return deps, settings, pool


async def _open(pool) -> None:
    if pool is not None:
        # Fails fast with a clear error when the database is unreachable.
        await pool.open(wait=True, timeout=15)


async def _close(pool) -> None:
    if pool is not None:
        await pool.close()


def _run(profile_path: str) -> int:
    from kit.app import build_app

    deps, settings, pool = _setup(profile_path)

    async def post_init(_app) -> None:
        await _open(pool)

    async def post_shutdown(_app) -> None:
        await _close(pool)

    app = build_app(
        deps,
        settings.telegram_bot_token.get_secret_value(),
        post_init=post_init,
        post_shutdown=post_shutdown,
    )
    log.info("polling as %s (%s)", deps.profile.name, deps.profile.slug)
    app.run_polling()
    return 0


def _serve(profile_path: str, host: str, port: int) -> int:
    import uvicorn

    from kit.web import create_app

    deps, settings, pool = _setup(profile_path)
    if settings.webhook_secret is None:
        raise _SetupError("WEBHOOK_SECRET is required for serve (scripts/setup_env.py makes one)")
    if settings.admin_api_token is None:
        log.warning("ADMIN_API_TOKEN not set: /api/leads is switched off")
    if settings.metrics_token is None:
        log.warning("METRICS_TOKEN not set: /metrics is switched off")
    api = create_app(
        deps,
        settings.telegram_bot_token.get_secret_value(),
        settings.webhook_secret.get_secret_value(),
        api_token=settings.admin_api_token.get_secret_value() if settings.admin_api_token else "",
        metrics_token=settings.metrics_token.get_secret_value() if settings.metrics_token else "",
        on_startup=lambda: _open(pool),
        on_shutdown=lambda: _close(pool),
    )
    log.info("webhook server for %s on %s:%s", deps.profile.slug, host, port)
    # log_config=None: uvicorn's lines go through kit.logs like every other.
    # No access log: the healthcheck and the Prometheus scrape would fill it,
    # and each update already gets its own line.
    uvicorn.run(api, host=host, port=port, log_level="info", log_config=None, access_log=False)
    return 0


def _migrate() -> int:
    from kit import logs
    from kit.db import migrate
    from kit.settings import DatabaseSettings

    settings = _settings(DatabaseSettings)
    logs.setup(settings.log_format)
    if settings.database_url is None:
        raise _SetupError("DATABASE_URL is not set: nothing to migrate")
    applied = migrate(settings.database_url.get_secret_value())
    print(f"migrations applied: {', '.join(applied)}" if applied else "migrations: up to date")
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
    sub.add_parser("migrate", help="apply pending migrations to DATABASE_URL")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return _validate(args.paths)
    try:
        if args.command == "run":
            return _run(args.profile)
        if args.command == "migrate":
            return _migrate()
        return _serve(args.profile, args.host, args.port)
    except _SetupError as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
