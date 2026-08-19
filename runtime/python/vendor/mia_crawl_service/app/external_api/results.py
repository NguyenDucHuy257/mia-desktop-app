from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.parsers.invoice_detail_excel_row_builder import InvoiceDetailExcelRowBuilder
from app.repositories.invoice_detail_repository import DETAIL_LINE_FIELDS
from app.config.runtime import RuntimeCapabilities


class InvalidResultCursorError(ValueError):
    pass


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def decode_cursor(value: str | None, expected: int) -> list[Any] | None:
    if not value:
        return None
    try:
        padded = value + '=' * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
    except Exception as error:
        raise InvalidResultCursorError('result cursor is invalid') from error
    if not isinstance(decoded, list) or len(decoded) != expected:
        raise InvalidResultCursorError('result cursor is invalid')
    return decoded


class JobResultReader:
    SORT_COLUMNS = (
        'nlap_date', 'direction', 'query_type', 'nbmst',
        'khmshdon', 'khhdon', 'shdon', 'id',
    )

    def __init__(
        self, database_path: Path | str,
        capabilities: RuntimeCapabilities | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.capabilities = capabilities or RuntimeCapabilities.from_environment()

    def overview_page(self, job, *, limit: int, cursor: str | None) -> dict:
        rows, total = self._index_page(
            'invoice_overview_items', job, limit=limit, cursor=cursor,
            extra_columns=(
                'company_tax_code, invoice_category, nlap, detail_fetched, '
                'detail_path, created_at, updated_at'
            ),
        )
        items = self._overview_database_items(rows)
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = items[:limit]
        next_cursor = self._cursor_for(visible[-1]) if has_more and visible else None
        return {
            'items': items, 'total_count': total,
            'invoice_count': total, 'row_count': len(items),
            'pagination': {
                'limit': limit, 'next_cursor': next_cursor,
                'has_more': has_more,
            },
        }

    def detail_page(self, job, *, limit: int, cursor: str | None) -> dict:
        if self._has_only_normalized_details(job):
            return self._normalized_detail_page(job, limit=limit, cursor=cursor)
        marker = decode_cursor(cursor, 9)
        invoice_marker = marker[:8] if marker else None
        line_marker = int(marker[8]) if marker else -1
        builder = InvoiceDetailExcelRowBuilder()
        output: list[dict[str, Any]] = []
        output_markers: list[list[Any]] = []
        invoice_count = self._count('invoice_detail_items', job)
        fetch_marker = invoice_marker
        include_marker_invoice = bool(invoice_marker)
        while len(output) <= limit:
            rows = self._query_rows(
                'invoice_detail_items', job, limit=50,
                marker=fetch_marker, inclusive=include_marker_invoice,
                extra_columns=self._detail_columns(),
            )
            if not rows:
                break
            advanced = False
            for row in rows:
                key = self._sort_key(row)
                # Advance over corrupt/missing artifacts as well; otherwise a
                # page containing only unreadable rows can hide later results.
                advanced = True
                fetch_marker = key
                start_line = line_marker + 1 if marker and key == marker[:8] else 0
                detail_rows = self._detail_result_rows(row, builder)
                for line_index, item in enumerate(detail_rows):
                    if line_index < start_line:
                        continue
                    output.append({'stt': line_index + 1, **self._public_record(item)})
                    output_markers.append([*key, line_index])
                    if len(output) > limit:
                        break
                if len(output) > limit:
                    break
            marker = None
            line_marker = -1
            include_marker_invoice = False
            if len(rows) < 50 or not advanced:
                break
            # Exclude the last fully visited invoice on the next keyset query.
            fetch_marker = list(fetch_marker) if fetch_marker else None
            if len(output) <= limit:
                rows = []
                continue
        has_more = len(output) > limit
        visible = output[:limit]
        next_cursor = (
            encode_cursor(output_markers[limit - 1])
            if has_more and limit else None
        )
        return {
            'items': visible, 'invoice_count': invoice_count,
            'row_count': len(visible),
            'pagination': {
                'limit': limit, 'next_cursor': next_cursor,
                'has_more': has_more,
            },
        }

    def _has_only_normalized_details(self, job) -> bool:
        if not self.database_path.is_file():
            return False
        directions = ','.join('?' for _ in job.parameters['directions'])
        queries = ','.join('?' for _ in job.parameters['query_types'])
        params = [
            job.company_tax_code, job.parameters['date_from'],
            job.parameters['date_to'], *job.parameters['directions'],
            *job.parameters['query_types'],
        ]
        with closing(self._connect()) as connection:
            columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                )
            }
            if 'normalized_ready' not in columns:
                return False
            normalized, legacy = connection.execute(f'''
                SELECT
                    COALESCE(SUM(CASE WHEN normalized_ready=1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN normalized_ready<>1 THEN 1 ELSE 0 END), 0)
                FROM invoice_detail_items
                WHERE company_tax_code=? AND nlap_date BETWEEN ? AND ?
                  AND direction IN ({directions}) AND query_type IN ({queries})
                  AND (error_message IS NULL OR TRIM(error_message)='')
                  AND (normalized_ready=1 OR TRIM(raw_detail_path)<>'')
            ''', params).fetchone()
        return int(normalized) > 0 and int(legacy) == 0

    def _normalized_detail_page(self, job, *, limit: int, cursor: str | None) -> dict:
        marker = decode_cursor(cursor, 9)
        directions = ','.join('?' for _ in job.parameters['directions'])
        queries = ','.join('?' for _ in job.parameters['query_types'])
        base_params: list[Any] = [
            job.company_tax_code, job.parameters['date_from'],
            job.parameters['date_to'], *job.parameters['directions'],
            *job.parameters['query_types'],
        ]
        params = list(base_params)
        sort = [f'd.{name}' for name in self.SORT_COLUMNS] + ['l.line_number']
        cursor_sql = ''
        if marker:
            cursor_sql = (
                f"AND ({','.join(sort)}) > "
                f"({','.join('?' for _ in marker)})"
            )
            params.extend(marker)
        params.append(limit + 1)
        selected_sort = ', '.join(
            f'd.{name} AS "sort_{name}"' for name in self.SORT_COLUMNS
        ) + ', l.line_number AS sort_line_number'
        selected = ', '.join(
            f'l."{name}" AS "result_{name}"' for name in DETAIL_LINE_FIELDS
        )
        with closing(self._connect()) as connection:
            rows = [dict(row) for row in connection.execute(f'''
                SELECT {selected_sort}, {selected}
                FROM invoice_detail_items d
                JOIN invoice_detail_lines l ON l.detail_item_id=d.id
                WHERE d.company_tax_code=? AND d.nlap_date BETWEEN ? AND ?
                  AND d.direction IN ({directions})
                  AND d.query_type IN ({queries})
                  AND d.normalized_ready=1
                  AND (d.error_message IS NULL OR TRIM(d.error_message)='')
                  {cursor_sql}
                ORDER BY {', '.join(sort)} LIMIT ?
            ''', params)]
            invoice_count = int(connection.execute(f'''
                SELECT COUNT(*) FROM invoice_detail_items d
                WHERE d.company_tax_code=? AND d.nlap_date BETWEEN ? AND ?
                  AND d.direction IN ({directions})
                  AND d.query_type IN ({queries})
                  AND d.normalized_ready=1
                  AND (d.error_message IS NULL OR TRIM(d.error_message)='')
            ''', base_params).fetchone()[0])
        has_more = len(rows) > limit
        items: list[dict[str, Any]] = []
        markers: list[list[Any]] = []
        for row in rows[:limit]:
            key = [row.pop(f'sort_{name}') for name in self.SORT_COLUMNS]
            line_number = int(row.pop('sort_line_number'))
            result = {
                name.removeprefix('result_'): value for name, value in row.items()
            }
            items.append({'stt': line_number, **self._public_record(result)})
            markers.append([*key, line_number])
        return {
            'items': items, 'invoice_count': invoice_count,
            'row_count': len(items),
            'pagination': {
                'limit': limit,
                'next_cursor': encode_cursor(markers[-1])
                if has_more and markers else None,
                'has_more': has_more,
            },
        }

    def _detail_result_rows(self, row, builder):
        raw_path = row.pop('raw_detail_path', None)
        normalized_ready = bool(row.pop('normalized_ready', 0))
        detail_id = int(row['id'])
        if normalized_ready:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='invoice_detail_lines'"
                ).fetchone()
                if not table:
                    return []
                columns = [
                    item['name'] for item in connection.execute(
                        'PRAGMA table_info(invoice_detail_lines)'
                    ) if item['name'] not in {
                        'id', 'detail_item_id', 'line_number',
                        'created_at', 'updated_at',
                    }
                ]
                selected = ', '.join(f'"{name}"' for name in columns)
                return [dict(item) for item in connection.execute(
                    f'''SELECT {selected} FROM invoice_detail_lines
                        WHERE detail_item_id=? ORDER BY line_number''',
                    (detail_id,),
                )]
        if not self.capabilities.retain_raw_artifacts:
            return []
        payload = self._read_json(raw_path)
        return [] if payload is None else builder.build_rows(payload, row)

    def _index_page(self, table, job, *, limit, cursor, extra_columns):
        marker = decode_cursor(cursor, 8)
        rows = self._query_rows(
            table, job, limit=limit + 1, marker=marker,
            extra_columns=extra_columns,
        )
        return rows, self._count(table, job)

    def _query_rows(
        self, table, job, *, limit, marker=None, extra_columns='', inclusive=False
    ):
        if not self.database_path.is_file():
            return []
        params: list[Any] = [
            job.company_tax_code, job.parameters['date_from'],
            job.parameters['date_to'], *job.parameters['directions'],
            *job.parameters['query_types'],
        ]
        directions = ','.join('?' for _ in job.parameters['directions'])
        queries = ','.join('?' for _ in job.parameters['query_types'])
        cursor_sql = ''
        if marker:
            operator = '>=' if inclusive else '>'
            cursor_sql = f"AND ({','.join(self.SORT_COLUMNS)}) {operator} ({','.join('?' for _ in marker)})"
            params.extend(marker)
        params.append(limit)
        columns = ', '.join(self.SORT_COLUMNS)
        if extra_columns:
            columns += ', ' + extra_columns
        sql = f"""
            SELECT {columns}
            FROM {table}
            WHERE company_tax_code = ? AND nlap_date BETWEEN ? AND ?
              AND direction IN ({directions}) AND query_type IN ({queries})
              {cursor_sql}
              {self._detail_availability_filter() if table == 'invoice_detail_items' else ''}
            ORDER BY {', '.join(self.SORT_COLUMNS)} LIMIT ?
        """
        with closing(self._connect()) as connection:
            try:
                return [dict(row) for row in connection.execute(sql, params)]
            except sqlite3.OperationalError:
                return []

    def _count(self, table, job):
        if not self.database_path.is_file():
            return 0
        directions = ','.join('?' for _ in job.parameters['directions'])
        queries = ','.join('?' for _ in job.parameters['query_types'])
        params = [
            job.company_tax_code, job.parameters['date_from'],
            job.parameters['date_to'], *job.parameters['directions'],
            *job.parameters['query_types'],
        ]
        availability = (
            self._detail_availability_filter()
            if table == 'invoice_detail_items' else ''
        )
        with closing(self._connect()) as connection:
            try:
                row = connection.execute(f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE company_tax_code = ? AND nlap_date BETWEEN ? AND ?
                      AND direction IN ({directions})
                      AND query_type IN ({queries})
                      {availability}
                """, params).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row[0])

    def _overview_database_items(self, rows):
        """Return complete persisted overview rows without reading raw artifacts."""
        attributes: dict[int, dict[str, Any]] = {}
        ids = [int(row['id']) for row in rows]
        if ids and self.database_path.is_file():
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='invoice_overview_attributes'"
                ).fetchone()
                if table:
                    placeholders = ','.join('?' for _ in ids)
                    for attribute in connection.execute(
                        f"""SELECT invoice_item_id, field_name, value_json
                            FROM invoice_overview_attributes
                            WHERE invoice_item_id IN ({placeholders})
                            ORDER BY invoice_item_id, field_name""",
                        ids,
                    ):
                        try:
                            value = json.loads(attribute['value_json'])
                        except (TypeError, json.JSONDecodeError):
                            continue
                        attributes.setdefault(
                            int(attribute['invoice_item_id']), {}
                        )[attribute['field_name']] = value

        items: list[dict[str, Any]] = []
        for row in rows:
            persisted = dict(row)
            invoice_item_id = int(persisted['id'])
            persisted['attributes'] = self._public_record(
                attributes.get(invoice_item_id, {})
            )
            items.append(persisted)
        return items

    @staticmethod
    def _read_json(path):
        if not path:
            return None
        try:
            value = json.loads(Path(str(path)).read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def _public_record(cls, value):
        blocked = {
            'password', 'token', 'authorization', 'proxy', 'proxy_url',
            'internal_session_id', 'session_hash', 'raw_json_path',
            'raw_detail_path', 'xml_path', 'database_path', 'db_path',
        }
        if isinstance(value, dict):
            return {
                key: cls._public_record(item)
                for key, item in value.items()
                if key.casefold() not in blocked
                and not key.casefold().endswith('_path')
            }
        if isinstance(value, list):
            return [cls._public_record(item) for item in value]
        return value

    @classmethod
    def _sort_key(cls, row):
        return [row[name] for name in cls.SORT_COLUMNS]

    @classmethod
    def _cursor_for(cls, row):
        return encode_cursor(cls._sort_key(row))

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _detail_columns(self):
        if not self.database_path.is_file():
            return ('invoice_category, raw_detail_path, error_message, '
                    '0 AS normalized_ready, NULL AS material_codes_json')
        with closing(self._connect()) as connection:
            columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                )
            }
        material = (
            'material_codes_json' if 'material_codes_json' in columns
            else 'NULL AS material_codes_json'
        )
        normalized = (
            'normalized_ready' if 'normalized_ready' in columns
            else '0 AS normalized_ready'
        )
        return (
            'invoice_category, raw_detail_path, error_message, '
            f'{normalized}, {material}'
        )

    def _detail_availability_filter(self):
        if not self.database_path.is_file():
            return "AND raw_detail_path <> '' AND (error_message IS NULL OR TRIM(error_message) = '')"
        with closing(self._connect()) as connection:
            columns = {
                row['name'] for row in connection.execute(
                    'PRAGMA table_info(invoice_detail_items)'
                )
            }
        ready = 'normalized_ready=1 OR ' if 'normalized_ready' in columns else ''
        if not self.capabilities.retain_raw_artifacts:
            return (
                "AND normalized_ready=1 "
                "AND (error_message IS NULL OR TRIM(error_message) = '')"
                if 'normalized_ready' in columns else 'AND 0=1'
            )
        return (
            f"AND ({ready}raw_detail_path <> '') "
            "AND (error_message IS NULL OR TRIM(error_message) = '')"
        )
