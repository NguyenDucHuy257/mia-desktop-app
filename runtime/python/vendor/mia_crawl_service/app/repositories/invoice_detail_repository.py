from __future__ import annotations

import sqlite3
from contextlib import closing
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
                       ON invoice_detail_lines(detail_item_id, line_number);"""
                )

    def replace_normalized_detail_success(
        self, *, company_tax_code: str, direction: str, query_type: str,
        invoice_category: str, nbmst: str, khhdon: str, shdon: str | int,
        khmshdon: str | int, nlap: str | None, nlap_date: str | None,
        raw_detail_path: Path | str, http_status: int | None, fetched_at: str,
        lines: list[dict[str, Any]],
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
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                connection.execute(
                    """INSERT INTO invoice_detail_items (
                           company_tax_code, direction, query_type, invoice_category,
                           nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                           raw_detail_path, http_status, fetched_at, created_at,
                           updated_at, error_message, normalized_ready
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
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
                           error_message=NULL, normalized_ready=1""",
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon), nlap,
                        normalized_date, str(raw_detail_path), http_status,
                        fetched_at, fetched_at, fetched_at,
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
                            *(line.get(field) for field in DETAIL_LINE_FIELDS),
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
                if updated != 1:
                    raise RuntimeError(
                        f'Expected one overview row while saving detail, updated={updated}'
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
        return dict(row) if row is not None else None

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
