"""Postgres plumbing: the connection pool and the migration runner.

Migrations are plain SQL files in migrations/, applied in name order. Each
file runs in its own transaction and is recorded in schema_migrations; the
whole run holds an advisory lock, so two containers starting at once do not
both apply the same file.

    python -m kit migrate
"""

import logging
from pathlib import Path

import psycopg
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# Any constant works; it only has to be the same for every runner.
_MIGRATION_LOCK = 4_243_001


def make_pool(conninfo: str, max_size: int = 5) -> AsyncConnectionPool:
    """Created closed: the caller opens it inside the running event loop."""
    return AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        # A connection that died with a database restart is replaced, not handed out.
        check=AsyncConnectionPool.check_connection,
    )


def migrate(conninfo: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies pending migrations and returns their names."""
    files = sorted(directory.glob("*.sql"))
    applied_now = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.execute(
                "create table if not exists schema_migrations ("
                " version text primary key,"
                " applied_at timestamptz not null default now())"
            )
            done = {row[0] for row in conn.execute("select version from schema_migrations")}
            for path in files:
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute(
                        "insert into schema_migrations (version) values (%s)", (path.name,)
                    )
                log.info("migration applied: %s", path.name)
                applied_now.append(path.name)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied_now
