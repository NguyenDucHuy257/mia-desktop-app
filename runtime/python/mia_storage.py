from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class StorageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def migration_directory() -> Path:
    return Path(__file__).resolve().parent / "migrations"


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> dict[str, Any]:
        if not self.database_path.is_absolute():
            raise StorageError("database_path_not_absolute")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                current = int(connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
                for script in sorted(migration_directory().glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(script.name.split("_", 1)[0])
                    sql = script.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                    existing = connection.execute("SELECT checksum FROM schema_migrations WHERE version = ?", (version,)).fetchone()
                    if existing:
                        if existing[0] != checksum:
                            raise StorageError("migration_checksum_mismatch")
                        continue
                    if version <= current:
                        raise StorageError("migration_history_invalid")
                    connection.executescript(f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations(version, checksum) VALUES ({version}, '{checksum}');\nCOMMIT;")
                    current = version
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise StorageError("database_corrupt")
                return {"schema_version": current, "integrity": integrity}
        except StorageError:
            raise
        except sqlite3.OperationalError as error:
            message = str(error).lower()
            if "locked" in message or "busy" in message:
                raise StorageError("database_locked") from None
            if "not a database" in message or "malformed" in message:
                raise StorageError("database_corrupt") from None
            raise StorageError("database_unavailable") from None
        except sqlite3.DatabaseError:
            raise StorageError("database_corrupt") from None

    def status(self) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                version = int(connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                return {"schema_version": version, "integrity": integrity}
        except sqlite3.DatabaseError:
            raise StorageError("database_corrupt") from None

    def create_account(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                existing = connection.execute(
                    "SELECT account_id, normalized_tax_code, status, created_at, updated_at FROM accounts WHERE normalized_tax_code = ?",
                    (value["tax_code"],),
                ).fetchone()
                if existing:
                    return self._account_row(existing, reused=True)
                connection.execute(
                    "INSERT INTO accounts(account_id, normalized_tax_code, encrypted_password, status, created_at, updated_at) VALUES (?, ?, ?, 'unchecked', ?, ?)",
                    (value["account_id"], value["tax_code"], value["encrypted_password"], value["timestamp"], value["timestamp"]),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT account_id, normalized_tax_code, status, created_at, updated_at FROM accounts WHERE account_id = ?",
                    (value["account_id"],),
                ).fetchone()
                return self._account_row(row, reused=False)
        except sqlite3.IntegrityError:
            raise StorageError("account_duplicate") from None
        except sqlite3.DatabaseError:
            raise StorageError("database_unavailable") from None

    def list_accounts(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT account_id, normalized_tax_code, status, created_at, updated_at FROM accounts ORDER BY created_at, account_id"
            ).fetchall()
            return [self._account_row(row, reused=False) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT account_id, normalized_tax_code, status, created_at, updated_at FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if not row:
                raise StorageError("account_not_found")
            return self._account_row(row, reused=False)

    def update_account(self, value: dict[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE accounts SET normalized_tax_code = ?, encrypted_password = ?, status = 'unchecked', updated_at = ? WHERE account_id = ?",
                (value["tax_code"], value["encrypted_password"], value["timestamp"], value["account_id"]),
            )
            if cursor.rowcount != 1:
                raise StorageError("account_not_found")
            connection.commit()
        return self.get_account(value["account_id"])

    def delete_account(self, account_id: str) -> None:
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
                if cursor.rowcount != 1:
                    raise StorageError("account_not_found")
                connection.commit()
        except sqlite3.IntegrityError:
            raise StorageError("account_in_use") from None

    @staticmethod
    def _account_row(row: tuple[Any, ...], reused: bool) -> dict[str, Any]:
        return {
            "connection_id": row[0],
            "username": row[1],
            "status": row[2],
            "token_generation": 0,
            "created_at": row[3],
            "updated_at": row[4],
            "reused": reused,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=0.2)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=200")
        return connection
