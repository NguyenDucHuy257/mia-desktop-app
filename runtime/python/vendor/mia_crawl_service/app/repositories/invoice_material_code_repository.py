from __future__ import annotations

import json

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any


class InvoiceMaterialCodeRepository:
    """Migrate, select and update material-code state in the local invoice DB."""

    COLUMN_NAME = 'material_codes_json'

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def migrate(self) -> None:
        if not self.database_path.is_file():
            raise FileNotFoundError(f'Invoice database is missing: {self.database_path}')
        with closing(self._connect()) as connection:
            if not self._table_exists(connection, 'invoice_detail_items'):
                raise RuntimeError('invoice_detail_items table does not exist')
            columns = {
                row['name']
                for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                ).fetchall()
            }
            if self.COLUMN_NAME not in columns:
                with connection:
                    connection.execute(
                        'ALTER TABLE invoice_detail_items '
                        'ADD COLUMN material_codes_json TEXT'
                    )

    def get_scope_records(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            has_overview = self._table_exists(connection, 'invoice_overview_items')
            has_package = self._table_exists(connection, 'invoice_package_items')
            overview_join = """
                LEFT JOIN invoice_overview_items AS overview
                  ON overview.company_tax_code = detail.company_tax_code
                 AND overview.direction = detail.direction
                 AND overview.query_type = detail.query_type
                 AND overview.nbmst = detail.nbmst
                 AND overview.khhdon = detail.khhdon
                 AND overview.shdon = detail.shdon
                 AND overview.khmshdon = detail.khmshdon
            """ if has_overview else ''
            package_join = """
                LEFT JOIN invoice_package_items AS package
                  ON package.company_tax_code = detail.company_tax_code
                 AND package.direction = detail.direction
                 AND package.query_type = detail.query_type
                 AND package.nbmst = detail.nbmst
                 AND package.khhdon = detail.khhdon
                 AND package.shdon = detail.shdon
                 AND package.khmshdon = detail.khmshdon
            """ if has_package else ''
            date_expression = (
                'COALESCE(detail.nlap_date, overview.nlap_date)'
                if has_overview else 'detail.nlap_date'
            )
            package_columns = (
                """package.id AS package_id,
                       package.xml_fetched,
                       package.xml_path,
                       package.error_message AS package_error_message,
                       COALESCE(package.unavailable, 0) AS package_unavailable,
                       package.unavailable_reason"""
                if has_package else
                """NULL AS package_id,
                       0 AS xml_fetched,
                       NULL AS xml_path,
                       NULL AS package_error_message,
                       0 AS package_unavailable,
                       NULL AS unavailable_reason"""
            )
            pending_clause = (
                "AND (detail.material_codes_json IS NULL "
                "OR TRIM(detail.material_codes_json) = '')"
                if pending_only else ''
            )
            has_normalized = 'normalized_ready' in {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                )
            }
            normalized_select = (
                'detail.normalized_ready' if has_normalized
                else '0 AS normalized_ready'
            )
            normalized_condition = 'detail.normalized_ready=1' if has_normalized else '0'
            rows = connection.execute(
                f"""
                SELECT detail.id,
                       detail.company_tax_code,
                       detail.direction,
                       detail.query_type,
                       detail.nbmst,
                       detail.khhdon,
                       detail.shdon,
                       detail.khmshdon,
                       {date_expression} AS nlap_date,
                       detail.raw_detail_path,
                       {normalized_select},
                       detail.material_codes_json,
                       {package_columns}
                FROM invoice_detail_items AS detail
                {overview_join}
                {package_join}
                WHERE detail.company_tax_code = ?
                  AND detail.direction = ?
                  AND detail.query_type = ?
                  AND {date_expression} BETWEEN ? AND ?
                  AND detail.error_message IS NULL
                  AND ({normalized_condition} OR (
                      detail.raw_detail_path IS NOT NULL
                      AND TRIM(detail.raw_detail_path) <> ''
                  ))
                  {pending_clause}
                ORDER BY {date_expression},
                         detail.id
                """,
                (company_tax_code, direction, query_type, from_date, to_date),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_normalized_detail_lines(self, detail_item_id: int) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            if not self._table_exists(connection, 'invoice_detail_lines'):
                return []
            rows = connection.execute(
                '''SELECT line_number, ten, dvtinh, sluong, dgia, thtien
                   FROM invoice_detail_lines
                   WHERE detail_item_id=? ORDER BY line_number''',
                (detail_item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute('BEGIN')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_material_codes(
        self,
        connection: sqlite3.Connection,
        record: dict[str, Any],
        material_codes_json: str,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE invoice_detail_items
            SET material_codes_json = ?,
                updated_at = datetime('now')
            WHERE company_tax_code = ?
              AND direction = ?
              AND query_type = ?
              AND nbmst = ?
              AND khhdon = ?
              AND shdon = ?
              AND khmshdon = ?
            """,
            (
                material_codes_json,
                record['company_tax_code'],
                record['direction'],
                record['query_type'],
                record['nbmst'],
                record['khhdon'],
                record['shdon'],
                record['khmshdon'],
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                'Expected exactly one invoice_detail_items row to be updated, '
                f'updated={cursor.rowcount}'
            )
        line_table = self._table_exists(connection, 'invoice_detail_lines')
        if not line_table:
            return
        detail_row = connection.execute(
            """SELECT id FROM invoice_detail_items
               WHERE company_tax_code=? AND direction=? AND query_type=?
                 AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
            tuple(record[name] for name in (
                'company_tax_code', 'direction', 'query_type', 'nbmst',
                'khhdon', 'shdon', 'khmshdon',
            )),
        ).fetchone()
        connection.execute(
            'UPDATE invoice_detail_lines SET m_VT = ? WHERE detail_item_id = ?',
            ('', detail_row['id']),
        )
        try:
            values = json.loads(material_codes_json)
        except (TypeError, json.JSONDecodeError):
            values = []
        for value in values if isinstance(values, list) else ():
            if (
                isinstance(value, dict)
                and value.get('match_status') == 'matched'
                and isinstance(value.get('line_index'), int)
            ):
                connection.execute(
                    """UPDATE invoice_detail_lines SET m_VT=?, updated_at=datetime('now')
                       WHERE detail_item_id=? AND line_number=?""",
                    (
                        str(value.get('material_code') or ''), detail_row['id'],
                        int(value['line_index']) + 1,
                    ),
                )

    def get_material_codes_json(self, record: dict[str, Any]) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT material_codes_json FROM invoice_detail_items WHERE id = ?',
                (record['id'],),
            ).fetchone()
        return None if row is None else row['material_codes_json']

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None
