from __future__ import annotations

import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.utils.date_utils import normalize_business_date


DETAIL_LINE_FIELDS = (
    'khmshdon', 'khhdon', 'shdon', 'ntao', 'nky', 'mhdon', 'dvtte', 'tgia',
    'nbten', 'nbmst', 'nbdchi', 'nmten', 'nmmst', 'nmdchi', 'm_VT', 'ten',
    'dvtinh', 'sluong', 'dgia', 'stckhau', 'tsuat', 'thtien', 'tthue',
    'ttcktmai', 'tgtphi', 'tgtttbso', 'tthai', 'ttxly', 'url', 'mk',
    'ghichu', 'thtttoan', 'tchat', 'dgiai', 'slo', 'hdung',
)


def _sqlite_value(value: Any) -> Any:
    """Persist Decimal values as canonical text instead of lossy SQLite REALs."""
    if isinstance(value, Decimal):
        return format(Decimal(0) if value == 0 else value, 'f')
    return value


class InvoiceDetailRepository:
    """Persist detail download results in the company overview database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS invoice_detail_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_tax_code TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        query_type TEXT NOT NULL,
                        invoice_category TEXT NOT NULL,
                        nbmst TEXT NOT NULL,
                        khhdon TEXT NOT NULL,
                        shdon TEXT NOT NULL,
                        khmshdon TEXT NOT NULL,
                        nlap TEXT,
                        nlap_date TEXT,
                        raw_detail_path TEXT NOT NULL,
                        http_status INTEGER,
                        fetched_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        error_message TEXT,
                        UNIQUE (
                            company_tax_code,
                            direction,
                            query_type,
                            nbmst,
                            khhdon,
                            shdon,
                            khmshdon
                        )
                    );

                    """
                )
                columns = {
                    row['name'] for row in connection.execute(
                        'PRAGMA table_info(invoice_detail_items)'
                    )
                }
                if 'nlap_date' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_detail_items ADD COLUMN nlap_date TEXT'
                    )
                if 'normalized_ready' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_detail_items '
                        'ADD COLUMN normalized_ready INTEGER NOT NULL DEFAULT 0'
                    )
                if 'detail_outcome' not in columns:
                    connection.execute(
                        "ALTER TABLE invoice_detail_items ADD COLUMN detail_outcome TEXT NOT NULL DEFAULT 'unknown'"
                    )
                if 'normalized_line_count' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_detail_items ADD COLUMN normalized_line_count INTEGER NOT NULL DEFAULT 0'
                    )
                self._backfill_missing_dates(connection)
                connection.executescript(
                    """CREATE INDEX IF NOT EXISTS idx_invoice_detail_range
                       ON invoice_detail_items (
                           company_tax_code, direction, query_type, nlap_date
                       );

                    CREATE TABLE IF NOT EXISTS invoice_detail_lines (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           detail_item_id INTEGER NOT NULL,
                           line_number INTEGER NOT NULL,
                           khmshdon, khhdon, shdon, ntao, nky, mhdon, dvtte, tgia,
                           nbten, nbmst, nbdchi, nmten, nmmst, nmdchi, m_VT, ten,
                           dvtinh, sluong, dgia, stckhau, tsuat, thtien, tthue,
                           ttcktmai, tgtphi, tgtttbso, tthai, ttxly, url, mk,
                           ghichu, thtttoan, tchat, dgiai, slo, hdung,
                           created_at TEXT NOT NULL,
                           updated_at TEXT NOT NULL,
                           FOREIGN KEY(detail_item_id) REFERENCES invoice_detail_items(id)
                               ON DELETE CASCADE,
                           UNIQUE(detail_item_id, line_number)
                       );

                       CREATE INDEX IF NOT EXISTS idx_invoice_detail_lines_page
                       ON invoice_detail_lines(detail_item_id, line_number);

                       CREATE TABLE IF NOT EXISTS invoice_detail_checkpoints (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           company_tax_code TEXT NOT NULL,
                           direction TEXT NOT NULL,
                           query_type TEXT NOT NULL,
                           from_date TEXT NOT NULL,
                           to_date TEXT NOT NULL,
                           checkpoint_status TEXT NOT NULL,
                           overview_expected INTEGER NOT NULL DEFAULT 0,
                           detail_succeeded INTEGER NOT NULL DEFAULT 0,
                           detail_failed INTEGER NOT NULL DEFAULT 0,
                           job_id TEXT NOT NULL,
                           started_at TEXT NOT NULL,
                           finalized_at TEXT,
                           updated_at TEXT NOT NULL,
                           UNIQUE(company_tax_code, direction, query_type, from_date, to_date)
                       );

                       CREATE INDEX IF NOT EXISTS idx_invoice_detail_checkpoint_range
                       ON invoice_detail_checkpoints(
                           company_tax_code, direction, query_type,
                           from_date, to_date, checkpoint_status
                       );"""
                )
                # Old rows with persisted lines can be proven complete.  A legacy
                # zero-line header remains unknown and must be refreshed; header
                # existence alone cannot prove that the portal returned a valid
                # empty invoice.
                connection.execute(
                    """UPDATE invoice_detail_items
                       SET detail_outcome='with_lines', normalized_line_count=(
                           SELECT COUNT(*) FROM invoice_detail_lines line
                           WHERE line.detail_item_id=invoice_detail_items.id
                       )
                       WHERE normalized_ready=1 AND detail_outcome='unknown'
                         AND EXISTS(SELECT 1 FROM invoice_detail_lines line
                                    WHERE line.detail_item_id=invoice_detail_items.id)"""
                )

    def begin_detail_checkpoint(
        self, *, company_tax_code: str, direction: str, query_type: str,
        from_date: str, to_date: str, overview_expected: int, job_id: str,
        timestamp: str,
    ) -> None:
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO invoice_detail_checkpoints(
                           company_tax_code,direction,query_type,from_date,to_date,
                           checkpoint_status,overview_expected,detail_succeeded,
                           detail_failed,job_id,started_at,finalized_at,updated_at
                       ) VALUES(?,?,?,?,?,'running',?,0,0,?,?,NULL,?)
                       ON CONFLICT(company_tax_code,direction,query_type,from_date,to_date)
                       DO UPDATE SET checkpoint_status='running',
                           overview_expected=excluded.overview_expected,
                           detail_succeeded=0,detail_failed=0,job_id=excluded.job_id,
                           started_at=excluded.started_at,finalized_at=NULL,
                           updated_at=excluded.updated_at""",
                    (company_tax_code, direction, query_type, from_date, to_date,
                     max(0, int(overview_expected)), job_id, timestamp, timestamp),
                )

    def finish_detail_checkpoint(
        self, *, company_tax_code: str, direction: str, query_type: str,
        from_date: str, to_date: str, job_id: str, timestamp: str,
    ) -> dict[str, Any]:
        """Finalize only when every current Overview identity has valid Detail."""
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                overview_rows = connection.execute(
                    """SELECT * FROM invoice_overview_items
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND nlap_date BETWEEN ? AND ?""",
                    (company_tax_code, direction, query_type, from_date, to_date),
                ).fetchall()
                from app.utils.invoice_identity import (canonical_invoice_identity,
                                                        invoice_status_is_excluded)
                has_attributes = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_overview_attributes'"
                ).fetchone() is not None
                eligible_rows = []
                for row in overview_rows:
                    status = None
                    if has_attributes:
                        import json
                        status_row = connection.execute(
                            "SELECT value_json FROM invoice_overview_attributes WHERE invoice_item_id=? AND field_name='tthai'",
                            (row['id'],),
                        ).fetchone()
                        if status_row:
                            try:
                                status = json.loads(status_row[0])
                            except (TypeError, json.JSONDecodeError):
                                status = status_row[0]
                    if not invoice_status_is_excluded(status):
                        eligible_rows.append(row)
                expected_ids = {
                    canonical_invoice_identity(company_tax_code, direction, dict(row))
                    for row in eligible_rows
                }
                detail_rows = connection.execute(
                    """SELECT * FROM invoice_detail_items WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ? AND normalized_ready=1
                         AND detail_outcome IN ('with_lines','valid_empty')
                         AND (error_message IS NULL OR TRIM(error_message)='')""",
                    (company_tax_code, direction, from_date, to_date),
                ).fetchall()
                detail_ids = {
                    canonical_invoice_identity(company_tax_code, direction, dict(row))
                    for row in detail_rows
                }
                expected = len(expected_ids)
                succeeded = len(expected_ids & detail_ids)
                failed = max(0, expected - succeeded)
                status = 'finalized' if failed == 0 else 'incomplete'
                connection.execute(
                    """UPDATE invoice_detail_checkpoints
                       SET checkpoint_status=?,overview_expected=?,detail_succeeded=?,
                           detail_failed=?,job_id=?,finalized_at=?,updated_at=?
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND from_date=? AND to_date=?""",
                    (status, expected, succeeded, failed, job_id,
                     timestamp if status == 'finalized' else None, timestamp,
                     company_tax_code, direction, query_type, from_date, to_date),
                )
        return {"status": status, "overview_expected": expected,
                "detail_succeeded": succeeded, "detail_failed": failed}

    def mark_detail_checkpoint_status(
        self, *, company_tax_code: str, job_id: str, status: str,
        timestamp: str,
    ) -> int:
        if status not in {'incomplete', 'failed', 'cancelled'}:
            raise ValueError('unsupported detail checkpoint status')
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """UPDATE invoice_detail_checkpoints SET checkpoint_status=?,updated_at=?
                       WHERE company_tax_code=? AND job_id=?
                         AND checkpoint_status IN ('running','incomplete')""",
                    (status, timestamp, company_tax_code, job_id),
                )
                return int(cursor.rowcount)

    def invalidate_detail_checkpoint(
        self, *, company_tax_code: str, direction: str, query_type: str,
        from_date: str, to_date: str, job_id: str, timestamp: str,
    ) -> int:
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """UPDATE invoice_detail_checkpoints
                       SET checkpoint_status='incomplete',job_id=?,finalized_at=NULL,
                           updated_at=?
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND NOT (to_date < ? OR from_date > ?)""",
                    (job_id, timestamp, company_tax_code, direction, query_type,
                     from_date, to_date),
                )
                return int(cursor.rowcount)

    def replace_normalized_detail_success(
        self, *, company_tax_code: str, direction: str, query_type: str,
        invoice_category: str, nbmst: str, khhdon: str, shdon: str | int,
        khmshdon: str | int, nlap: str | None, nlap_date: str | None,
        raw_detail_path: Path | str, http_status: int | None, fetched_at: str,
        lines: list[dict[str, Any]], detail_outcome: str | None = None,
    ) -> None:
        """Atomically replace normalized detail header/lines and overview status."""
        self.init_db()
        key = (
            company_tax_code, direction, query_type, str(nbmst), str(khhdon),
            str(shdon), str(khmshdon),
        )
        normalized_date = normalize_business_date(nlap) or normalize_business_date(
            nlap_date
        )
        outcome = detail_outcome or ('with_lines' if lines else 'valid_empty')
        if outcome not in {'with_lines', 'valid_empty'}:
            raise ValueError('unsupported detail outcome')
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                connection.execute(
                    """INSERT INTO invoice_detail_items (
                           company_tax_code, direction, query_type, invoice_category,
                           nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                           raw_detail_path, http_status, fetched_at, created_at,
                           updated_at, error_message, normalized_ready,
                           detail_outcome, normalized_line_count
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
                       ON CONFLICT (
                           company_tax_code, direction, query_type,
                           nbmst, khhdon, shdon, khmshdon
                       ) DO UPDATE SET
                           invoice_category=excluded.invoice_category,
                           nlap=excluded.nlap, nlap_date=excluded.nlap_date,
                           raw_detail_path=excluded.raw_detail_path,
                           http_status=excluded.http_status,
                           fetched_at=excluded.fetched_at,
                           updated_at=excluded.updated_at,
                           error_message=NULL, normalized_ready=1,
                           detail_outcome=excluded.detail_outcome,
                           normalized_line_count=excluded.normalized_line_count""",
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon), nlap,
                        normalized_date, str(raw_detail_path), http_status,
                        fetched_at, fetched_at, fetched_at, outcome, len(lines),
                    ),
                )
                detail_id = int(connection.execute(
                    """SELECT id FROM invoice_detail_items
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
                    key,
                ).fetchone()[0])
                connection.execute(
                    'DELETE FROM invoice_detail_lines WHERE detail_item_id = ?',
                    (detail_id,),
                )
                if lines:
                    columns = ', '.join(DETAIL_LINE_FIELDS)
                    placeholders = ', '.join('?' for _ in DETAIL_LINE_FIELDS)
                    connection.executemany(
                        f"""INSERT INTO invoice_detail_lines (
                               detail_item_id, line_number, {columns}, created_at, updated_at
                           ) VALUES (?, ?, {placeholders}, ?, ?)""",
                        [(
                            detail_id, index,
                            *(_sqlite_value(line.get(field)) for field in DETAIL_LINE_FIELDS),
                            fetched_at, fetched_at,
                        ) for index, line in enumerate(lines, start=1)],
                    )
                updated = connection.execute(
                    """UPDATE invoice_overview_items
                       SET detail_fetched=1, detail_path=?, updated_at=?
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
                    (str(raw_detail_path), fetched_at, *key),
                ).rowcount
                if updated == 0:
                    from app.utils.invoice_identity import canonical_invoice_identity
                    wanted = canonical_invoice_identity(company_tax_code, direction, {
                        'nbmst': nbmst, 'khhdon': khhdon, 'shdon': shdon,
                        'khmshdon': khmshdon,
                    })
                    candidate_ids = [int(item['id']) for item in connection.execute(
                        "SELECT * FROM invoice_overview_items WHERE company_tax_code=? AND direction=?",
                        (company_tax_code, direction),
                    ).fetchall() if canonical_invoice_identity(company_tax_code, direction, dict(item)) == wanted]
                    if candidate_ids:
                        placeholders = ','.join('?' for _ in candidate_ids)
                        updated = connection.execute(
                            f"UPDATE invoice_overview_items SET detail_fetched=1, detail_path=?, updated_at=? WHERE id IN ({placeholders})",
                            (str(raw_detail_path), fetched_at, *candidate_ids),
                        ).rowcount
                if updated < 1:
                    raise RuntimeError(
                        f'Expected a canonical overview row while saving detail, updated={updated}'
                    )

    def upsert_detail_success(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
        nlap: str | None,
        nlap_date: str | None,
        raw_detail_path: Path | str,
        http_status: int | None,
        fetched_at: str,
    ) -> None:
        self.init_db()
        nlap_date = normalize_business_date(nlap) or normalize_business_date(nlap_date)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO invoice_detail_items (
                        company_tax_code, direction, query_type, invoice_category,
                        nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                        raw_detail_path, http_status, fetched_at, created_at,
                        updated_at, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT (
                        company_tax_code, direction, query_type,
                        nbmst, khhdon, shdon, khmshdon
                    ) DO UPDATE SET
                        invoice_category = excluded.invoice_category,
                        nlap = excluded.nlap,
                        nlap_date = excluded.nlap_date,
                        raw_detail_path = excluded.raw_detail_path,
                        http_status = excluded.http_status,
                        fetched_at = excluded.fetched_at,
                        updated_at = excluded.updated_at,
                        error_message = NULL
                    """,
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon),
                        nlap, nlap_date, str(raw_detail_path), http_status,
                        fetched_at, fetched_at, fetched_at,
                    ),
                )

    def upsert_detail_error(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
        nlap: str | None,
        nlap_date: str | None,
        http_status: int | None,
        error_message: str,
        attempted_at: str,
    ) -> None:
        self.init_db()
        nlap_date = normalize_business_date(nlap) or normalize_business_date(nlap_date)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO invoice_detail_items (
                        company_tax_code, direction, query_type, invoice_category,
                        nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                        raw_detail_path, http_status, fetched_at, created_at,
                        updated_at, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
                    ON CONFLICT (
                        company_tax_code, direction, query_type,
                        nbmst, khhdon, shdon, khmshdon
                    ) DO UPDATE SET
                        nlap = excluded.nlap,
                        nlap_date = excluded.nlap_date,
                        http_status = excluded.http_status,
                        updated_at = excluded.updated_at,
                        error_message = excluded.error_message
                    """,
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon),
                        nlap, nlap_date, http_status, attempted_at, attempted_at,
                        attempted_at, error_message[:2000],
                    ),
                )

    def mark_overview_detail_fetched(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
        raw_detail_path: Path | str,
        updated_at: str,
    ) -> int:
        """Mark exactly one matching overview key as successfully fetched."""
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE invoice_overview_items
                    SET detail_fetched = 1,
                        detail_path = ?,
                        updated_at = ?
                    WHERE company_tax_code = ?
                      AND direction = ?
                      AND query_type = ?
                      AND nbmst = ?
                      AND khhdon = ?
                      AND shdon = ?
                      AND khmshdon = ?
                    """,
                    (
                        str(raw_detail_path), updated_at, company_tax_code,
                        direction, query_type, str(nbmst), str(khhdon),
                        str(shdon), str(khmshdon),
                    ),
                )
                return cursor.rowcount

    def get_detail_by_invoice_key(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
    ) -> dict[str, Any] | None:
        self.init_db()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM invoice_detail_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nbmst = ?
                  AND khhdon = ?
                  AND shdon = ?
                  AND khmshdon = ?
                """,
                (
                    company_tax_code, direction, query_type, str(nbmst),
                    str(khhdon), str(shdon), str(khmshdon),
                ),
            ).fetchone()
            if row is None:
                from app.utils.invoice_identity import canonical_invoice_identity
                wanted = canonical_invoice_identity(company_tax_code, direction, {
                    'nbmst': nbmst, 'khhdon': khhdon, 'shdon': shdon,
                    'khmshdon': khmshdon,
                })
                candidates = connection.execute(
                    "SELECT * FROM invoice_detail_items WHERE company_tax_code=? AND direction=?",
                    (company_tax_code, direction),
                ).fetchall()
                row = next((item for item in candidates
                            if canonical_invoice_identity(company_tax_code, direction, dict(item)) == wanted), None)
        return dict(row) if row is not None else None

    def taxable_total_by_invoice_key(
        self, company_tax_code: str, direction: str, query_type: str,
        nbmst: str, khhdon: str, shdon: str | int, khmshdon: str | int,
    ) -> Decimal | None:
        """Sum exact product-line amounts from a verified normalized detail."""
        detail = self.get_detail_by_invoice_key(
            company_tax_code, direction, query_type,
            nbmst, khhdon, shdon, khmshdon,
        )
        if not detail or detail.get('error_message') or not detail.get('normalized_ready'):
            return None
        outcome = str(detail.get('detail_outcome') or '')
        if outcome == 'valid_empty':
            return Decimal(0)
        if outcome != 'with_lines':
            return None
        with closing(self._connect()) as connection:
            rows = connection.execute(
                'SELECT thtien FROM invoice_detail_lines '
                'WHERE detail_item_id=? ORDER BY line_number',
                (int(detail['id']),),
            ).fetchall()
        total = Decimal(0)
        try:
            for row in rows:
                value = row['thtien']
                if value not in (None, ''):
                    total += Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
        return total

    def mark_missing_detail_file(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
        *,
        updated_at: str,
    ) -> int:
        """Atomically make a DB success with no valid raw file pending again."""
        self.init_db()
        key = (
            company_tax_code, direction, query_type, str(nbmst), str(khhdon),
            str(shdon), str(khmshdon),
        )
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE invoice_detail_items
                    SET raw_detail_path = '',
                        error_message = ?,
                        updated_at = ?
                    WHERE company_tax_code = ? AND direction = ? AND query_type = ?
                      AND nbmst = ? AND khhdon = ? AND shdon = ? AND khmshdon = ?
                    """,
                    (
                        'Local detail file is missing or invalid; queued for re-download',
                        updated_at,
                        *key,
                    ),
                )
                connection.execute(
                    """
                    UPDATE invoice_overview_items
                    SET detail_fetched = 0, detail_path = NULL, updated_at = ?
                    WHERE company_tax_code = ? AND direction = ? AND query_type = ?
                      AND nbmst = ? AND khhdon = ? AND shdon = ? AND khmshdon = ?
                    """,
                    (updated_at, *key),
                )
                return int(cursor.rowcount)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _backfill_missing_dates(
        connection: sqlite3.Connection, *, batch_size: int = 500,
    ) -> None:
        rows = connection.execute(
            """SELECT id, nlap FROM invoice_detail_items
               WHERE nlap_date IS NULL ORDER BY id LIMIT ?""",
            (batch_size,),
        ).fetchall()
        updates = [
            (normalized, row['id'])
            for row in rows
            if (normalized := normalize_business_date(row['nlap'])) is not None
        ]
        if updates:
            connection.executemany(
                'UPDATE invoice_detail_items SET nlap_date = ? WHERE id = ?',
                updates,
            )
from decimal import Decimal, InvalidOperation
