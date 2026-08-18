from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


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

                    CREATE INDEX IF NOT EXISTS idx_invoice_detail_range
                    ON invoice_detail_items (
                        company_tax_code,
                        direction,
                        query_type,
                        nlap_date
                    );
                    """
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
