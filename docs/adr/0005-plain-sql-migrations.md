# 5. Plain SQL migrations

Status: accepted

## Context

The schema is two tables. The project uses raw SQL through psycopg, with no ORM, so a migration tool built around models would add a dependency and a layer between the reader and the SQL.

## Decision

Migrations are numbered `.sql` files in `migrations/`, applied by `python -m kit migrate`. Each file runs in its own transaction and is recorded in `schema_migrations`; the whole run holds `pg_advisory_lock`, so two containers starting at once do not apply the same file twice. In compose, migrations are a one-shot service and the bot starts only after it exits 0; the bot itself never migrates.

## Consequences

- The schema is readable as is, constraints and indexes included.
- A failing migration leaves no trace: its transaction rolls back and it is not recorded.
- There are no down migrations. A mistake is fixed by the next migration, which is how production databases are changed anyway.
