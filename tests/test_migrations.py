"""The migration runner, each test in its own throwaway schema."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from kit.db import MIGRATIONS_DIR, migrate
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
