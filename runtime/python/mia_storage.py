from __future__ import annotations

import hashlib
import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
TERMINAL_JOB_STATUSES = {"completed", "completed_with_warning", "failed", "cancelled", "abandoned"}
JOB_TRANSITIONS = {
    "queued": {"waiting_account", "running", "cancelling", "cancelled", "failed", "abandoned"},
    "waiting_account": {"queued", "running", "cancelling", "cancelled", "failed", "abandoned"},
    "running": {"cancelling", "completed", "completed_with_warning", "failed", "abandoned"},
    "cancelling": {"cancelled", "failed", "abandoned"},
}


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
                    "SELECT account_id, normalized_tax_code, status, created_at, updated_at, company_name FROM accounts WHERE normalized_tax_code = ?",
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
                    "SELECT account_id, normalized_tax_code, status, created_at, updated_at, company_name FROM accounts WHERE account_id = ?",
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
                "SELECT account_id, normalized_tax_code, status, created_at, updated_at, company_name FROM accounts ORDER BY created_at, account_id"
            ).fetchall()
            return [self._account_row(row, reused=False) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT account_id, normalized_tax_code, status, created_at, updated_at, company_name FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if not row:
                raise StorageError("account_not_found")
            return self._account_row(row, reused=False)

    def get_account_secret(self, account_id: str) -> dict[str, str]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT normalized_tax_code, encrypted_password FROM accounts WHERE account_id = ?", (account_id,),
            ).fetchone()
            if not row:
                raise StorageError("account_not_found")
            return {"username": row[0], "encrypted_password": row[1]}

    def update_account_company(self, account_id: str, company_name: str, timestamp: str) -> None:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE accounts SET company_name=?, status='connected', updated_at=? WHERE account_id=?",
                (company_name[:300] or None, timestamp, account_id),
            )
            if cursor.rowcount != 1:
                raise StorageError("account_not_found")
            connection.commit()

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

    def create_job(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT job_id FROM jobs WHERE idempotency_key = ?", (value["idempotency_key"],)
                ).fetchone()
                if existing:
                    connection.commit()
                    return self._get_job(connection, existing[0], reused=True)
                account = connection.execute(
                    "SELECT 1 FROM accounts WHERE account_id = ?", (value["connection_id"],)
                ).fetchone()
                if not account:
                    raise StorageError("account_not_found")
                connection.execute(
                    "INSERT INTO jobs(job_id, account_id, idempotency_key, intent_json, status, overall_percent, stage, event_sequence, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', 0, 'queued', 1, ?, ?)",
                    (value["job_id"], value["connection_id"], value["idempotency_key"], json.dumps(value["intent"], separators=(",", ":"), sort_keys=True), value["timestamp"], value["timestamp"]),
                )
                connection.execute(
                    "INSERT INTO job_events(job_id, sequence, event_type, payload_json, created_at) VALUES (?, 1, 'queued', '{}', ?)",
                    (value["job_id"], value["timestamp"]),
                )
                connection.commit()
                return self._get_job(connection, value["job_id"], reused=False)
        except StorageError:
            raise
        except sqlite3.IntegrityError:
            raise StorageError("job_conflict") from None

    def resume_job(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE status NOT IN ('completed','completed_with_warning','failed','cancelled','abandoned') ORDER BY updated_at DESC, job_id DESC LIMIT 1"
            ).fetchone()
            return self._get_job(connection, row[0], reused=True) if row else None

    def get_job(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return self._get_job(connection, job_id, reused=True)

    def cancel_job(self, job_id: str, timestamp: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._get_job(connection, job_id, reused=True)
            if current["status"] in TERMINAL_JOB_STATUSES or current["status"] == "cancelling":
                connection.commit()
                return current
            target = "cancelled" if current["status"] in {"queued", "waiting_account"} else "cancelling"
            sequence = current["event_sequence"] + 1
            connection.execute(
                "UPDATE jobs SET status = ?, stage = ?, event_sequence = ?, updated_at = ? WHERE job_id = ?",
                (target, target, sequence, timestamp, job_id),
            )
            connection.execute(
                "INSERT INTO job_events(job_id, sequence, event_type, payload_json, created_at) VALUES (?, ?, ?, '{}', ?)",
                (job_id, sequence, target, timestamp),
            )
            connection.commit()
            return self._get_job(connection, job_id, reused=True)

    def transition_job(self, value: dict[str, Any]) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._get_job(connection, value["job_id"], reused=True)
            expected = value["expected_sequence"]
            target = value["status"]
            if expected != current["event_sequence"]:
                raise StorageError("stale_job_update")
            if target == current["status"]:
                connection.commit()
                return current
            if target not in JOB_TRANSITIONS.get(current["status"], set()):
                raise StorageError("invalid_job_transition")
            progress = max(0, min(100, int(value.get("overall_percent", current["overall_percent"]))))
            month = value.get("current_month")
            if month is not None:
                month = dict(month)
                month["percent"] = max(0, min(100, int(month.get("percent", 0))))
            sequence = expected + 1
            payload = {"overall_percent": progress, "current_month": month}
            connection.execute(
                "UPDATE jobs SET status=?, stage=?, overall_percent=?, current_month_json=?, error_json=?, event_sequence=?, updated_at=? WHERE job_id=?",
                (target, value.get("stage"), progress, json.dumps(month, separators=(",", ":")) if month else None,
                 json.dumps(value.get("error"), separators=(",", ":")) if value.get("error") else None,
                 sequence, value["timestamp"], value["job_id"]),
            )
            connection.execute(
                "INSERT INTO job_events(job_id, sequence, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (value["job_id"], sequence, target, json.dumps(payload, separators=(",", ":")), value["timestamp"]),
            )
            connection.commit()
            return self._get_job(connection, value["job_id"], reused=True)

    def update_job_progress(self, job_id: str, percent: int, stage: str, timestamp: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            current = self._get_job(connection, job_id, reused=True)
            if current["status"] != "running":
                return current
            progress = max(current["overall_percent"], min(99, max(0, int(percent))))
            connection.execute(
                "UPDATE jobs SET overall_percent=?, stage=?, updated_at=? WHERE job_id=?",
                (progress, stage[:128], timestamp, job_id),
            )
            connection.commit()
            return self._get_job(connection, job_id, reused=True)

    def job_summary(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            job = self._get_job(connection, job_id, reused=True)
            events = connection.execute(
                "SELECT sequence, event_type, payload_json, created_at FROM job_events WHERE job_id = ? ORDER BY sequence",
                (job_id,),
            ).fetchall()
            return {"job_id": job_id, "status": job["status"], "warning_count": 0, "events": [
                {"sequence": row[0], "type": row[1], "payload": json.loads(row[2]), "created_at": row[3]} for row in events
            ]}

    def clear_terminal_jobs(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM jobs WHERE status IN ('completed','completed_with_warning','failed','cancelled','abandoned')")
            connection.commit()

    def import_overviews(self, value: dict[str, Any]) -> dict[str, int]:
        items = value["items"]
        if not isinstance(items, list) or len(items) > 5000:
            raise StorageError("invalid_result_batch")
        inserted = 0
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in items:
                cursor = connection.execute(
                    "INSERT INTO invoice_overviews(account_id,direction,business_key,payload_json,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(account_id,direction,business_key) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                    (value["connection_id"], item["direction"], item["business_key"], json.dumps(item["payload"], ensure_ascii=False, separators=(",", ":")), value["timestamp"]),
                )
                inserted += 1 if cursor.rowcount else 0
            connection.commit()
        return {"processed": len(items), "upserted": inserted}

    def query_overviews(self, value: dict[str, Any]) -> dict[str, Any]:
        limit = int(value.get("limit", 50))
        if not 1 <= limit <= 200:
            raise StorageError("invalid_result_limit")
        after = self._decode_cursor(value.get("cursor"))
        direction = value.get("direction")
        search = str(value.get("search", "")).strip().casefold()
        if direction not in (None, "purchase", "sold"):
            raise StorageError("invalid_result_filter")
        clauses, params = ["account_id=?", "overview_id>?"], [value["connection_id"], after]
        if direction:
            clauses.append("direction=?")
            params.append(direction)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT overview_id,direction,business_key,payload_json FROM invoice_overviews WHERE {' AND '.join(clauses)} ORDER BY overview_id",
                params,
            ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row[3])
            if search and search not in json.dumps(payload, ensure_ascii=False).casefold() and search not in row[2].casefold():
                continue
            items.append({"overview_id": row[0], "direction": row[1], "business_key": row[2], "payload": payload})
            if len(items) > limit:
                break
        page, has_more = items[:limit], len(items) > limit
        return {"items": page, "pagination": {"limit": limit, "has_more": has_more, "next_cursor": self._encode_cursor(page[-1]["overview_id"]) if has_more and page else None}}

    def import_details(self, value: dict[str, Any]) -> dict[str, int]:
        items = value["items"]
        if not isinstance(items, list) or len(items) > 10000:
            raise StorageError("invalid_result_batch")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in items:
                overview = connection.execute(
                    "SELECT overview_id FROM invoice_overviews WHERE account_id=? AND direction=? AND business_key=?",
                    (value["connection_id"], item["direction"], item["business_key"]),
                ).fetchone()
                if not overview:
                    raise StorageError("overview_not_found")
                connection.execute(
                    "INSERT INTO invoice_details(overview_id,line_key,payload_json) VALUES (?,?,?) ON CONFLICT(overview_id,line_key) DO UPDATE SET payload_json=excluded.payload_json",
                    (overview[0], item["line_key"], json.dumps(item["payload"], ensure_ascii=False, separators=(",", ":"))),
                )
            connection.commit()
        return {"processed": len(items)}

    def query_details(self, value: dict[str, Any]) -> dict[str, Any]:
        limit = int(value.get("limit", 50))
        if not 1 <= limit <= 200:
            raise StorageError("invalid_result_limit")
        after = self._decode_cursor(value.get("cursor"))
        direction = value.get("direction")
        search = str(value.get("search", "")).strip().casefold()
        if direction not in (None, "purchase", "sold"):
            raise StorageError("invalid_result_filter")
        clauses, params = ["o.account_id=?", "d.detail_id>?"], [value["connection_id"], after]
        if direction:
            clauses.append("o.direction=?")
            params.append(direction)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT d.detail_id,o.direction,o.business_key,d.line_key,d.payload_json FROM invoice_details d JOIN invoice_overviews o ON o.overview_id=d.overview_id WHERE {' AND '.join(clauses)} ORDER BY d.detail_id",
                params,
            ).fetchall()
        items = []
        for row in rows:
            payload = json.loads(row[4])
            if search and search not in json.dumps(payload, ensure_ascii=False).casefold() and search not in row[2].casefold():
                continue
            items.append({"detail_id": row[0], "direction": row[1], "business_key": row[2], "line_key": row[3], "payload": payload})
            if len(items) > limit:
                break
        page, has_more = items[:limit], len(items) > limit
        return {"items": page, "pagination": {"limit": limit, "has_more": has_more, "next_cursor": self._encode_cursor(page[-1]["detail_id"]) if has_more and page else None}}

    @staticmethod
    def _encode_cursor(value: int) -> str:
        return base64.urlsafe_b64encode(f"v1:{value}".encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(value: Any) -> int:
        if value in (None, ""):
            return 0
        if not isinstance(value, str) or len(value) > 64:
            raise StorageError("invalid_result_cursor")
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            prefix, identifier = decoded.split(":", 1)
            if prefix != "v1" or not identifier.isdigit():
                raise ValueError
            return int(identifier)
        except (ValueError, UnicodeError):
            raise StorageError("invalid_result_cursor") from None

    @staticmethod
    def _get_job(connection: sqlite3.Connection, job_id: str, reused: bool) -> dict[str, Any]:
        row = connection.execute(
            "SELECT job_id, account_id, idempotency_key, intent_json, status, overall_percent, stage, current_month_json, error_json, event_sequence, created_at, updated_at FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            raise StorageError("job_not_found")
        return {
            "job_id": row[0], "connection_id": row[1], "idempotency_key": row[2],
            "intent": json.loads(row[3]), "status": row[4], "overall_percent": max(0, min(100, row[5])),
            "stage": row[6], "current_month": json.loads(row[7]) if row[7] else None,
            "error": json.loads(row[8]) if row[8] else None, "event_sequence": row[9],
            "created_at": row[10], "updated_at": row[11], "reused": reused,
        }

    @staticmethod
    def _account_row(row: tuple[Any, ...], reused: bool) -> dict[str, Any]:
        return {
            "connection_id": row[0],
            "username": row[1],
            "status": row[2],
            "token_generation": 0,
            "created_at": row[3],
            "updated_at": row[4],
            "company_name": row[5] if len(row) > 5 else None,
            "reused": reused,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=0.2)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=200")
        return connection
