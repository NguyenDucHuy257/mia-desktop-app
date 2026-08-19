from __future__ import annotations


_MIGRATION_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock("
    "hashtext('mia:control-schema-migration:v1'))"
)


def acquire_control_schema_migration_lock(connection) -> None:
    """Serialize control-schema DDL across MIA PostgreSQL processes.

    Every production service may run idempotent migrations at startup. PostgreSQL
    can still deadlock those DDL transactions when services start concurrently.
    This transaction-scoped advisory lock makes migrations enter one at a time and
    is released automatically on commit, rollback, or process loss.
    """
    connection.execute("SET LOCAL statement_timeout = '30s'")
    connection.execute("SET LOCAL lock_timeout = '30s'")
    connection.execute(_MIGRATION_LOCK_SQL)
