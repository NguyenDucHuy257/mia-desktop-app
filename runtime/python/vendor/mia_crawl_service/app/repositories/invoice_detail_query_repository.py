from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any


class InvoiceDetailQueryRepository:
    """Read detail export indexes without opening raw JSON files."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def get_detail_records_for_export(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        self._validate_date_range(from_date, to_date)
        if not self.database_path.is_file():
            return []
        with closing(self._connect()) as connection:
            if not self._table_exists(connection, 'invoice_detail_items'):
                return []
            columns = {
                row['name']
                for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                ).fetchall()
            }
            material_codes_select = (
                'material_codes_json'
                if 'material_codes_json' in columns
                else 'NULL AS material_codes_json'
            )
            rows = connection.execute(
                f"""
                SELECT company_tax_code, direction, query_type, invoice_category,
                       nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                       raw_detail_path, {material_codes_select}
                FROM invoice_detail_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nlap_date BETWEEN ? AND ?
                  AND raw_detail_path IS NOT NULL
                  AND raw_detail_path <> ''
                ORDER BY nlap_date DESC,
                         CASE WHEN shdon GLOB '[0-9]*' THEN CAST(shdon AS INTEGER) END ASC,
                         shdon ASC,
                         id ASC
                """,
                (company_tax_code, direction, query_type, from_date, to_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_missing_detail_records(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> int:
        """Count overview rows without a usable successful detail index."""
        self._validate_date_range(from_date, to_date)
        if not self.database_path.is_file():
            return 0
        with closing(self._connect()) as connection:
            if not self._table_exists(connection, 'invoice_overview_items'):
                return 0
            has_detail_table = self._table_exists(connection, 'invoice_detail_items')
            if not has_detail_table:
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
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM invoice_overview_items AS overview
                LEFT JOIN invoice_detail_items AS detail
                  ON detail.company_tax_code = overview.company_tax_code
                 AND detail.direction = overview.direction
                 AND detail.query_type = overview.query_type
                 AND detail.nbmst = overview.nbmst
                 AND detail.khhdon = overview.khhdon
                 AND detail.shdon = overview.shdon
                 AND detail.khmshdon = overview.khmshdon
                WHERE overview.company_tax_code = ?
                  AND overview.direction = ?
                  AND overview.query_type = ?
                  AND overview.nlap_date BETWEEN ? AND ?
                  AND (
                      overview.detail_fetched = 0
                      OR overview.detail_path IS NULL
                      OR detail.raw_detail_path IS NULL
                      OR detail.raw_detail_path = ''
                  )
                """,
                (company_tax_code, direction, query_type, from_date, to_date),
            ).fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

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
