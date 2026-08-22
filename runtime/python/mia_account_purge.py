from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable


OWNER_ID = "mia-desktop-local"


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _safe_child(root: Path, relative: str | Path) -> Path | None:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def _delete_file_if_safe(root: Path, relative: str) -> bool:
    target = _safe_child(root, relative)
    if target is None or not target.is_file():
        return False
    target.unlink(missing_ok=True)
    return True


def _finalize_secure_sqlite_delete(connection: sqlite3.Connection) -> None:
    # secure_delete overwrites deleted cells instead of leaving their payload in
    # freelist pages. Truncating WAL and VACUUMing makes the destructive account
    # action match the user's expectation that local account data is removed.
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("VACUUM")


def _purge_legacy_database(data_dir: Path, account_id: str) -> tuple[list[str], list[str], int]:
    database = data_dir / "mia.sqlite3"
    if not database.is_file():
        return [], [], 0
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA secure_delete=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        job_ids = [
            str(row[0]) for row in connection.execute(
                "SELECT job_id FROM jobs WHERE account_id=?", (account_id,)
            ).fetchall()
        ] if _table_exists(connection, "jobs") else []
        artifact_paths = [
            str(row[0]) for row in connection.execute(
                "SELECT relative_path FROM artifacts WHERE account_id=?", (account_id,)
            ).fetchall()
        ] if _table_exists(connection, "artifacts") else []
        if _table_exists(connection, "jobs"):
            connection.execute("DELETE FROM jobs WHERE account_id=?", (account_id,))
        deleted = 0
        if _table_exists(connection, "accounts"):
            deleted = connection.execute(
                "DELETE FROM accounts WHERE account_id=?", (account_id,)
            ).rowcount
        connection.commit()
        _finalize_secure_sqlite_delete(connection)
        return job_ids, artifact_paths, deleted
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _purge_production_control(data_dir: Path, account_id: str, tax_code: str) -> list[str]:
    database = data_dir / "source-control.sqlite3"
    if not database.is_file():
        return []
    connection = sqlite3.connect(database, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA secure_delete=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        connection.execute("BEGIN IMMEDIATE")
        job_ids: list[str] = []
        if _table_exists(connection, "crawl_jobs"):
            job_ids = [
                str(row[0]) for row in connection.execute(
                    "SELECT job_id FROM crawl_jobs WHERE account_key=? OR company_tax_code=?",
                    (account_id, tax_code),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM crawl_jobs WHERE account_key=? OR company_tax_code=?",
                (account_id, tax_code),
            )

        session_hashes: list[str] = []
        if _table_exists(connection, "account_connections"):
            session_hashes = [
                str(row[0]) for row in connection.execute(
                    "SELECT session_hash FROM account_connections WHERE owner_id=? AND username=?",
                    (OWNER_ID, tax_code),
                ).fetchall()
            ]
            connection.execute(
                "DELETE FROM account_connections WHERE owner_id=? AND username=?",
                (OWNER_ID, tax_code),
            )

        source_account_ids: list[str] = []
        if _table_exists(connection, "source_accounts"):
            source_account_ids = [
                str(row[0]) for row in connection.execute(
                    "SELECT account_id FROM source_accounts WHERE account_key=?",
                    (tax_code.strip().casefold(),),
                ).fetchall()
            ]

        if _table_exists(connection, "internal_sessions"):
            if session_hashes:
                placeholders = ",".join("?" for _ in session_hashes)
                connection.execute(
                    f"DELETE FROM internal_sessions WHERE session_hash IN ({placeholders})",
                    session_hashes,
                )
            if source_account_ids:
                placeholders = ",".join("?" for _ in source_account_ids)
                connection.execute(
                    f"DELETE FROM internal_sessions WHERE account_id IN ({placeholders})",
                    source_account_ids,
                )

        if source_account_ids and _table_exists(connection, "source_accounts"):
            placeholders = ",".join("?" for _ in source_account_ids)
            connection.execute(
                f"DELETE FROM source_accounts WHERE account_id IN ({placeholders})",
                source_account_ids,
            )
        connection.commit()
        _finalize_secure_sqlite_delete(connection)
        return job_ids
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def purge_account_data(data_dir: Path, account_id: str, tax_code: str) -> dict[str, object]:
    """Remove durable data owned by one desktop account from both storage stacks."""
    data_dir = data_dir.resolve()
    legacy_job_ids, artifact_paths, legacy_account_rows = _purge_legacy_database(
        data_dir, account_id
    )
    production_job_ids = _purge_production_control(data_dir, account_id, tax_code)

    artifact_files_removed = sum(
        1 for relative in artifact_paths if _delete_file_if_safe(data_dir, relative)
    )
    source_directory = _safe_child(data_dir / "source-data", tax_code)
    source_directory_removed = False
    if source_directory is not None and source_directory.exists():
        shutil.rmtree(source_directory)
        source_directory_removed = True

    return {
        "account_id": account_id,
        "tax_code": tax_code,
        "job_ids": sorted(set(legacy_job_ids + production_job_ids)),
        "legacy_account_rows": legacy_account_rows,
        "artifact_files_removed": artifact_files_removed,
        "source_directory_removed": source_directory_removed,
    }


def scrub_account_log_lines(data_dir: Path, identifiers: Iterable[str]) -> int:
    """Remove log lines that can be attributed to this account without touching others."""
    needles = tuple(sorted({str(value) for value in identifiers if len(str(value)) >= 6}))
    if not needles:
        return 0
    removed = 0
    directories = (data_dir / "logs", data_dir.parent / "logs")
    for directory in directories:
        if not directory.is_dir():
            continue
        for filename in directory.glob("*.log*"):
            if not filename.is_file():
                continue
            try:
                lines = filename.read_text(encoding="utf-8", errors="replace").splitlines(True)
                kept = [line for line in lines if not any(needle in line for needle in needles)]
                difference = len(lines) - len(kept)
                if difference <= 0:
                    continue
                temporary = filename.with_name(f".{filename.name}.purge-{os.getpid()}.tmp")
                temporary.write_text("".join(kept), encoding="utf-8")
                os.replace(temporary, filename)
                removed += difference
            except OSError:
                # Purging account data must not corrupt global diagnostics if a
                # concurrent writer briefly owns a Windows file handle.
                continue
    return removed
