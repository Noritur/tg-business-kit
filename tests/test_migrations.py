"""The migration runner, each test in its own throwaway schema."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest

from kit.db import MIGRATIONS_DIR, make_pool, migrate
from kit.storage import Delivery, PostgresUpdateLog
from tests.conftest import _need_pg

ALL = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))


@pytest.fixture
def schema_url():
    """A conninfo whose search_path points at a new, empty schema."""
    url = _need_pg()
    schema = f"mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'create schema "{schema}"')
    yield f"{url}?options=-csearch_path%3D{schema}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'drop schema "{schema}" cascade')


def test_first_run_applies_everything_in_order(schema_url):
    assert migrate(schema_url) == ALL
    with psycopg.connect(schema_url) as conn:
        versions = [row[0] for row in conn.execute("select version from schema_migrations")]
    assert sorted(versions) == ALL


def test_second_run_changes_nothing(schema_url):
    migrate(schema_url)
    assert migrate(schema_url) == []


def test_two_runners_at_once_apply_each_file_once(schema_url):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(migrate, [schema_url, schema_url]))
    assert sorted(results[0] + results[1]) == ALL


def test_a_failing_migration_leaves_no_trace(schema_url, tmp_path):
    (tmp_path / "001_ok.sql").write_text("create table ok (id int);")
    (tmp_path / "002_broken.sql").write_text("create table half (id int); select 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrate(schema_url, tmp_path)
    with psycopg.connect(schema_url) as conn:
        versions = [row[0] for row in conn.execute("select version from schema_migrations")]
        half = conn.execute("select to_regclass('half')").fetchone()[0]
    assert versions == ["001_ok.sql"] and half is None


def _copy(directory, *names: str) -> None:
    """Puts real migrations into `directory`, to migrate a schema up to a point."""
    for name in names:
        (directory / name).write_text((MIGRATIONS_DIR / name).read_text())


def test_leads_from_before_the_tap_column_have_none_and_a_tap_is_unique(schema_url, tmp_path):
    _copy(tmp_path, "001_leads.sql", "002_processed_updates.sql", "003_update_claims.sql")
    migrate(schema_url, tmp_path)
    insert = (
        "insert into leads (client_slug, tg_user_id, service, service_label, slot_date, slot_time)"
        " values ('dental', 7, 'chk', 'Check-up', '2026-10-20', '09:00')"
    )
    with psycopg.connect(schema_url) as conn:
        conn.execute(insert)
        conn.execute(insert)
    _copy(tmp_path, "004_lead_query.sql")
    assert migrate(schema_url, tmp_path) == ["004_lead_query.sql"]

    with psycopg.connect(schema_url) as conn:
        assert [row[0] for row in conn.execute("select tg_query_id from leads")] == [None, None]
        conn.execute("update leads set tg_query_id = 'tap-1' where id = 1")
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute("update leads set tg_query_id = 'tap-1' where id = 2")


async def test_updates_recorded_before_claims_existed_stay_done(schema_url, tmp_path):
    _copy(tmp_path, "001_leads.sql", "002_processed_updates.sql")
    migrate(schema_url, tmp_path)
    with psycopg.connect(schema_url) as conn:
        conn.execute("insert into processed_updates (bot, update_id) values ('bot-a', 7)")
    _copy(tmp_path, "003_update_claims.sql")
    assert migrate(schema_url, tmp_path) == ["003_update_claims.sql"]

    pool = make_pool(schema_url)
    await pool.open(wait=True, timeout=10)
    try:
        # A zero window: only done_at keeps the old row from being handled again.
        log = PostgresUpdateLog(pool, stale_after=timedelta(0))
        assert await log.claim("bot-a", 7) is Delivery.DONE
    finally:
        await pool.close()
