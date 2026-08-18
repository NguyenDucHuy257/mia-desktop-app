from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any


class InvoiceOverviewRepository:
    """Persist normalized invoice overview keys in a company-scoped SQLite DB."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                CREATE TABLE IF NOT EXISTS invoice_overview_items (
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
                    raw_json_path TEXT NOT NULL,
                    detail_fetched INTEGER NOT NULL DEFAULT 0,
                    detail_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (
                        company_tax_code,
                        direction,
                        query_type,
                        nbmst,
                        khhdon,
                        shdon,
                        khmshdon
                    )
                )
                    """
                )
                columns = {
                    row['name']
                    for row in connection.execute('PRAGMA table_info(invoice_overview_items)')
                }
                if 'nlap_date' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_overview_items ADD COLUMN nlap_date TEXT'
                    )
                # Backfill legacy ISO timestamps so existing overview data can
                # immediately participate in date-range detail downloads.
                connection.execute(
                    """
                    UPDATE invoice_overview_items
                    SET nlap_date = substr(nlap, 1, 10)
                    WHERE nlap_date IS NULL
                      AND nlap GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
                    """
                )
                connection.executescript(
                    """
                    CREATE INDEX IF NOT EXISTS idx_invoice_overview_pending_detail
                    ON invoice_overview_items (
                        company_tax_code,
                        detail_fetched,
                        direction,
                        query_type
                    );

                    CREATE INDEX IF NOT EXISTS idx_invoice_overview_detail_range
                    ON invoice_overview_items (
                        company_tax_code,
                        direction,
                        query_type,
                        nlap_date
                    );
                    """
                )

    def upsert_items(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        raw_json_path: Path | str,
        items: Sequence[dict[str, Any]],
        timestamp: str,
    ) -> int:
        """Insert/update overview keys and return the number of processed items."""
        self.init_db()
        if not items:
            return 0

        rows = [
            (
                company_tax_code,
                direction,
                query_type,
                invoice_category,
                str(item['nbmst']),
                str(item['khhdon']),
                str(item['shdon']),
                str(item['khmshdon']),
                item.get('nlap'),
                item.get('nlap_date'),
                str(raw_json_path),
                timestamp,
                timestamp,
            )
            for item in items
        ]

        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    """
                INSERT INTO invoice_overview_items (
                    company_tax_code,
                    direction,
                    query_type,
                    invoice_category,
                    nbmst,
                    khhdon,
                    shdon,
                    khmshdon,
                    nlap,
                    nlap_date,
                    raw_json_path,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    company_tax_code,
                    direction,
                    query_type,
                    nbmst,
                    khhdon,
                    shdon,
                    khmshdon
                ) DO UPDATE SET
                    nlap = excluded.nlap,
                    nlap_date = excluded.nlap_date,
                    raw_json_path = excluded.raw_json_path,
                    updated_at = excluded.updated_at
                """,
                    rows,
                )
        return len(rows)

    def count_overview_items(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> int:
        """Count locally indexed overview rows in an inclusive date range."""
        self._validate_date_range(from_date, to_date)
        self.init_db()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM invoice_overview_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nlap_date BETWEEN ? AND ?
                """,
                (company_tax_code, direction, query_type, from_date, to_date),
            ).fetchone()
        return int(row[0])

    def get_items_for_detail_download(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        only_pending: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return overview keys eligible for detail download."""
        self._validate_date_range(from_date, to_date)
        if limit is not None and limit <= 0:
            raise ValueError('limit must be greater than zero')
        self.init_db()
        conditions = [
            'company_tax_code = ?',
            'direction = ?',
            'query_type = ?',
            'nlap_date BETWEEN ? AND ?',
        ]
        parameters: list[Any] = [
            company_tax_code,
            direction,
            query_type,
            from_date,
            to_date,
        ]
        if only_pending:
            conditions.append('(detail_fetched = 0 OR detail_path IS NULL)')
        sql = (
            'SELECT id, company_tax_code, direction, query_type, invoice_category, '
            'nbmst, khhdon, shdon, khmshdon, nlap, nlap_date '
            'FROM invoice_overview_items WHERE '
            + ' AND '.join(conditions)
            + ' ORDER BY nlap_date, id'
        )
        if limit is not None:
            sql += ' LIMIT ?'
            parameters.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_pending_detail_items(
        self,
        *,
        company_tax_code: str,
        direction: str | None = None,
        query_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return overview rows that have not had their detail downloaded."""
        if limit is not None and limit <= 0:
            raise ValueError('limit must be greater than zero')

        self.init_db()
        conditions = ['company_tax_code = ?', 'detail_fetched = 0']
        parameters: list[Any] = [company_tax_code]
        if direction is not None:
            conditions.append('direction = ?')
            parameters.append(direction)
        if query_type is not None:
            conditions.append('query_type = ?')
            parameters.append(query_type)

        sql = (
            'SELECT * FROM invoice_overview_items WHERE '
            + ' AND '.join(conditions)
            + ' ORDER BY id'
        )
        if limit is not None:
            sql += ' LIMIT ?'
            parameters.append(limit)

        with closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _validate_date_range(from_date: str, to_date: str) -> None:
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
