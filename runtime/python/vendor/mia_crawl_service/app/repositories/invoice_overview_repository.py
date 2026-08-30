from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from app.utils.date_utils import normalize_business_date


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
                self._backfill_missing_dates(connection)
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

                    CREATE TABLE IF NOT EXISTS invoice_overview_attributes (
                        invoice_item_id INTEGER NOT NULL,
                        field_name TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        FOREIGN KEY(invoice_item_id) REFERENCES invoice_overview_items(id)
                            ON DELETE CASCADE,
                        PRIMARY KEY(invoice_item_id, field_name)
                    );

                    CREATE TABLE IF NOT EXISTS invoice_overview_refresh_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        refresh_run_id TEXT NOT NULL,
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
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(refresh_run_id, company_tax_code, direction, query_type,
                               nbmst, khhdon, shdon, khmshdon)
                    );

                    CREATE TABLE IF NOT EXISTS invoice_overview_refresh_attributes (
                        refresh_item_id INTEGER NOT NULL,
                        field_name TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        FOREIGN KEY(refresh_item_id)
                            REFERENCES invoice_overview_refresh_items(id) ON DELETE CASCADE,
                        PRIMARY KEY(refresh_item_id, field_name)
                    );

                    CREATE TABLE IF NOT EXISTS invoice_overview_checkpoints (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company_tax_code TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        query_type TEXT NOT NULL,
                        from_date TEXT NOT NULL,
                        to_date TEXT NOT NULL,
                        status_filter TEXT NOT NULL,
                        next_state TEXT,
                        page_number INTEGER NOT NULL DEFAULT 0,
                        fetched_count INTEGER NOT NULL DEFAULT 0,
                        expected_total INTEGER,
                        last_page_size INTEGER NOT NULL,
                        checkpoint_status TEXT NOT NULL,
                        last_raw_page_path TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (
                            company_tax_code,
                            direction,
                            query_type,
                            from_date,
                            to_date,
                            status_filter
                        )
                    );

                    CREATE INDEX IF NOT EXISTS idx_invoice_overview_checkpoint_resume
                    ON invoice_overview_checkpoints (
                        company_tax_code,
                        direction,
                        query_type,
                        checkpoint_status,
                        from_date,
                        to_date
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
                self._normalized_item_date(item),
                str(raw_json_path),
                timestamp,
                timestamp,
            )
            for item in items
        ]

        with closing(self._connect()) as connection:
            with connection:
                self._upsert_rows(connection, rows)
                self._replace_public_attributes(
                    connection, company_tax_code, direction, query_type, items
                )
        return len(rows)

    def commit_overview_refresh_page(
        self, *, refresh_run_id: str, company_tax_code: str, direction: str,
        query_type: str, invoice_category: str, from_date: str, to_date: str,
        status_filter: str, page_number: int, page_size: int, fetched_count: int,
        expected_total: int | None, next_state: str | None,
        checkpoint_status: str, items: Sequence[dict[str, Any]], timestamp: str,
        raw_json_path: Path | str = '',
    ) -> int:
        """Stage a refresh page and checkpoint it atomically without changing active rows."""
        if not refresh_run_id:
            raise ValueError('refresh_run_id is required')
        self.init_db()
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                for item in items:
                    connection.execute(
                        """INSERT INTO invoice_overview_refresh_items (
                               refresh_run_id, company_tax_code, direction, query_type,
                               invoice_category, nbmst, khhdon, shdon, khmshdon,
                               nlap, nlap_date, created_at, updated_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(refresh_run_id, company_tax_code, direction,
                                       query_type, nbmst, khhdon, shdon, khmshdon)
                           DO UPDATE SET invoice_category=excluded.invoice_category,
                               nlap=excluded.nlap, nlap_date=excluded.nlap_date,
                               updated_at=excluded.updated_at""",
                        (
                            refresh_run_id, company_tax_code, direction, query_type,
                            invoice_category, str(item['nbmst']), str(item['khhdon']),
                            str(item['shdon']), str(item['khmshdon']), item.get('nlap'),
                            self._normalized_item_date(item), timestamp, timestamp,
                        ),
                    )
                    refresh_item_id = int(connection.execute(
                        """SELECT id FROM invoice_overview_refresh_items
                           WHERE refresh_run_id=? AND company_tax_code=?
                             AND direction=? AND query_type=? AND nbmst=?
                             AND khhdon=? AND shdon=? AND khmshdon=?""",
                        (
                            refresh_run_id, company_tax_code, direction, query_type,
                            str(item['nbmst']), str(item['khhdon']), str(item['shdon']),
                            str(item['khmshdon']),
                        ),
                    ).fetchone()[0])
                    fields = item.get('_public_fields')
                    if isinstance(fields, dict):
                        connection.execute(
                            'DELETE FROM invoice_overview_refresh_attributes '
                            'WHERE refresh_item_id=?', (refresh_item_id,),
                        )
                        connection.executemany(
                            """INSERT INTO invoice_overview_refresh_attributes
                               (refresh_item_id, field_name, value_json) VALUES (?, ?, ?)""",
                            [
                                (refresh_item_id, str(name), json.dumps(value, ensure_ascii=False))
                                for name, value in fields.items()
                            ],
                        )
                self._upsert_checkpoint(
                    connection, company_tax_code=company_tax_code,
                    direction=direction, query_type=query_type,
                    from_date=from_date, to_date=to_date,
                    status_filter=status_filter, next_state=next_state,
                    page_number=page_number, fetched_count=fetched_count,
                    expected_total=expected_total, page_size=page_size,
                    checkpoint_status=checkpoint_status, raw_json_path='',
                    timestamp=timestamp,
                )
        return len(items)

    def reconcile_split_refresh_checkpoint(
        self, *, refresh_run_id: str, company_tax_code: str,
        direction: str, query_type: str, from_date: str, to_date: str,
        status_filter: str, fetched_count: int,
        fallback_expected_total: int | None, page_number: int,
        page_size: int, next_state: str | None, result_status: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """Close a parent checkpoint after recursive date-range fallback.

        Child ranges own their durable cursors, but refresh activation is keyed
        to the original requested range. A recovered split therefore reconciles
        that parent checkpoint without weakening activation's terminal guard.
        """
        self._validate_date_range(from_date, to_date)
        if result_status not in {'completed', 'completed_with_warning'}:
            raise ValueError('split refresh result must be terminal')
        if fetched_count < 0:
            raise ValueError('fetched_count must not be negative')
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                existing = connection.execute(
                    """SELECT * FROM invoice_overview_checkpoints
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND from_date=? AND to_date=? AND status_filter=?""",
                    (company_tax_code, direction, query_type, from_date,
                     to_date, status_filter),
                ).fetchone()
                staged_count = int(connection.execute(
                    """SELECT COUNT(*) FROM invoice_overview_refresh_items
                       WHERE refresh_run_id=? AND company_tax_code=?
                         AND direction=? AND query_type=?
                         AND nlap_date BETWEEN ? AND ?""",
                    (refresh_run_id, company_tax_code, direction, query_type,
                     from_date, to_date),
                ).fetchone()[0])
                parent_expected = (
                    int(existing['expected_total'])
                    if existing is not None and existing['expected_total'] is not None
                    else None
                )
                expected_total = (
                    parent_expected if parent_expected is not None
                    else (
                        int(fallback_expected_total)
                        if fallback_expected_total is not None
                        else staged_count
                    )
                )
                reconciled_fetched = min(int(fetched_count), staged_count)
                if expected_total is not None:
                    reconciled_fetched = min(reconciled_fetched, int(expected_total))
                checkpoint_status = (
                    'completed'
                    if (
                        result_status == 'completed'
                        and parent_expected is not None
                        and reconciled_fetched == parent_expected
                    )
                    else 'completed_with_warning'
                )
                existing_page = (
                    int(existing['page_number'] or 0) if existing is not None else 0
                )
                existing_path = (
                    existing['last_raw_page_path'] if existing is not None else ''
                )
                self._upsert_checkpoint(
                    connection,
                    company_tax_code=company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    from_date=from_date,
                    to_date=to_date,
                    status_filter=status_filter,
                    next_state=next_state,
                    page_number=max(existing_page, int(page_number), 1),
                    fetched_count=reconciled_fetched,
                    expected_total=expected_total,
                    page_size=max(int(page_size), 1),
                    checkpoint_status=checkpoint_status,
                    raw_json_path=existing_path or '',
                    timestamp=timestamp,
                )
                return {
                    'checkpoint_status': checkpoint_status,
                    'fetched_count': reconciled_fetched,
                    'expected_total': expected_total,
                    'staged_count': staged_count,
                }

    def activate_overview_refresh(
        self, *, refresh_run_id: str, company_tax_code: str, direction: str,
        query_type: str, from_date: str, to_date: str,
        status_filters: Sequence[str], timestamp: str,
    ) -> int:
        """Atomically publish a verified refresh and remove stale active invoices."""
        self.init_db()
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                has_warning = False
                for status_filter in status_filters:
                    row = connection.execute(
                        """SELECT checkpoint_status, fetched_count, expected_total
                           FROM invoice_overview_checkpoints
                           WHERE company_tax_code=? AND direction=? AND query_type=?
                             AND from_date=? AND to_date=? AND status_filter=?""",
                        (company_tax_code, direction, query_type, from_date, to_date,
                         status_filter),
                    ).fetchone()
                    if (
                        row is None
                        or row['checkpoint_status'] not in {
                            'completed', 'completed_with_warning'
                        }
                        or (
                            row['checkpoint_status'] == 'completed'
                            and row['expected_total'] is not None
                            and int(row['fetched_count']) != int(row['expected_total'])
                        )
                    ):
                        raise RuntimeError('refresh checkpoint is not complete')
                    has_warning = has_warning or (
                        row['checkpoint_status'] == 'completed_with_warning'
                    )
                staged = connection.execute(
                    """SELECT * FROM invoice_overview_refresh_items
                       WHERE refresh_run_id=? AND company_tax_code=?
                         AND direction=? AND query_type=?
                         AND nlap_date BETWEEN ? AND ?""",
                    (refresh_run_id, company_tax_code, direction, query_type,
                     from_date, to_date),
                ).fetchall()
                for item in staged:
                    self._upsert_rows(connection, [(
                        company_tax_code, direction, query_type,
                        item['invoice_category'], item['nbmst'], item['khhdon'],
                        item['shdon'], item['khmshdon'], item['nlap'],
                        item['nlap_date'], '', timestamp, timestamp,
                    )])
                    active_id = int(connection.execute(
                        """SELECT id FROM invoice_overview_items
                           WHERE company_tax_code=? AND direction=? AND query_type=?
                             AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
                        (company_tax_code, direction, query_type, item['nbmst'],
                         item['khhdon'], item['shdon'], item['khmshdon']),
                    ).fetchone()[0])
                    connection.execute(
                        'DELETE FROM invoice_overview_attributes WHERE invoice_item_id=?',
                        (active_id,),
                    )
                    connection.execute(
                        """INSERT INTO invoice_overview_attributes
                           (invoice_item_id, field_name, value_json)
                           SELECT ?, field_name, value_json
                           FROM invoice_overview_refresh_attributes
                           WHERE refresh_item_id=?""",
                        (active_id, item['id']),
                    )
                stale = connection.execute(
                    """SELECT id, company_tax_code, direction, query_type,
                              nbmst, khhdon, shdon, khmshdon
                       FROM invoice_overview_items active
                       WHERE company_tax_code=? AND direction=? AND query_type=?
                         AND nlap_date BETWEEN ? AND ?
                         AND NOT EXISTS (
                           SELECT 1 FROM invoice_overview_refresh_items staged
                           WHERE staged.refresh_run_id=?
                             AND staged.company_tax_code=active.company_tax_code
                             AND staged.direction=active.direction
                             AND staged.query_type=active.query_type
                             AND staged.nbmst=active.nbmst AND staged.khhdon=active.khhdon
                             AND staged.shdon=active.shdon AND staged.khmshdon=active.khmshdon
                         )""",
                    (company_tax_code, direction, query_type, from_date, to_date,
                     refresh_run_id),
                ).fetchall()
                stale_ids = [int(row['id']) for row in stale]
                # A source-total warning means the source may have omitted rows.
                # Merge staged records but retain previously active records that
                # were not returned during this incomplete refresh.
                if stale_ids and not has_warning:
                    detail_table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='invoice_detail_items'"
                    ).fetchone()
                    for stale_item in stale if detail_table else ():
                        detail = connection.execute(
                            """SELECT id FROM invoice_detail_items
                               WHERE company_tax_code=? AND direction=? AND query_type=?
                                 AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
                            tuple(stale_item[name] for name in (
                                'company_tax_code', 'direction', 'query_type', 'nbmst',
                                'khhdon', 'shdon', 'khmshdon',
                            )),
                        ).fetchone()
                        if detail is not None:
                            line_table = connection.execute(
                                "SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='invoice_detail_lines'"
                            ).fetchone()
                            if line_table:
                                connection.execute(
                                    'DELETE FROM invoice_detail_lines '
                                    'WHERE detail_item_id=?', (detail['id'],),
                                )
                            connection.execute(
                                'DELETE FROM invoice_detail_items WHERE id=?',
                                (detail['id'],),
                            )
                    placeholders = ','.join('?' for _ in stale_ids)
                    connection.execute(
                        f'DELETE FROM invoice_overview_attributes '
                        f'WHERE invoice_item_id IN ({placeholders})', stale_ids,
                    )
                    connection.execute(
                        f'DELETE FROM invoice_overview_items '
                        f'WHERE id IN ({placeholders})', stale_ids,
                    )
                placeholders = ','.join('?' for _ in status_filters)
                connection.execute(
                    f"""UPDATE invoice_overview_checkpoints
                        SET checkpoint_status='finalized', updated_at=?
                        WHERE company_tax_code=? AND direction=? AND query_type=?
                          AND from_date=? AND to_date=?
                          AND status_filter IN ({placeholders})""",
                    (timestamp, company_tax_code, direction, query_type,
                     from_date, to_date, *status_filters),
                )
                connection.execute(
                    'DELETE FROM invoice_overview_refresh_items WHERE refresh_run_id=?',
                    (refresh_run_id,),
                )
                return len(staged)

    def commit_overview_page(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        from_date: str,
        to_date: str,
        status_filter: str,
        page_number: int,
        page_size: int,
        fetched_count: int,
        expected_total: int | None,
        next_state: str | None,
        checkpoint_status: str,
        raw_json_path: Path | str,
        items: Sequence[dict[str, Any]],
        timestamp: str,
    ) -> int:
        """Atomically UPSERT one page of keys and advance its durable cursor."""
        self._validate_date_range(from_date, to_date)
        if page_number < 1:
            raise ValueError('page_number must be at least 1')
        if page_size < 1:
            raise ValueError('page_size must be at least 1')
        if fetched_count < 0:
            raise ValueError('fetched_count must not be negative')
        if expected_total is not None and expected_total < 0:
            raise ValueError('expected_total must not be negative')
        if not status_filter:
            raise ValueError('status_filter must not be empty')
        if checkpoint_status not in {
            'in_progress', 'completed', 'completed_with_warning', 'incomplete'
        }:
            raise ValueError(f'Unsupported checkpoint_status: {checkpoint_status!r}')

        self.init_db()
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
                self._normalized_item_date(item),
                str(raw_json_path),
                timestamp,
                timestamp,
            )
            for item in items
        ]
        with closing(self._connect()) as connection:
            with connection:
                self._upsert_rows(connection, rows)
                self._replace_public_attributes(
                    connection, company_tax_code, direction, query_type, items
                )
                connection.execute(
                    """
                    INSERT INTO invoice_overview_checkpoints (
                        company_tax_code, direction, query_type,
                        from_date, to_date, status_filter, next_state,
                        page_number, fetched_count, expected_total,
                        last_page_size, checkpoint_status, last_raw_page_path,
                        error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    ON CONFLICT (
                        company_tax_code, direction, query_type,
                        from_date, to_date, status_filter
                    ) DO UPDATE SET
                        next_state = excluded.next_state,
                        page_number = excluded.page_number,
                        fetched_count = excluded.fetched_count,
                        expected_total = excluded.expected_total,
                        last_page_size = excluded.last_page_size,
                        checkpoint_status = excluded.checkpoint_status,
                        last_raw_page_path = excluded.last_raw_page_path,
                        error_message = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        company_tax_code,
                        direction,
                        query_type,
                        from_date,
                        to_date,
                        status_filter,
                        next_state,
                        page_number,
                        fetched_count,
                        expected_total,
                        page_size,
                        checkpoint_status,
                        str(raw_json_path),
                        timestamp,
                        timestamp,
                    ),
                )
        return len(rows)

    def get_overview_checkpoint(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        status_filter: str,
    ) -> dict[str, Any] | None:
        self._validate_date_range(from_date, to_date)
        self.init_db()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM invoice_overview_checkpoints
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND from_date = ?
                  AND to_date = ?
                  AND status_filter = ?
                """,
                (
                    company_tax_code, direction, query_type,
                    from_date, to_date, status_filter,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def finalize_overview_checkpoint(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        status_filter: str,
        timestamp: str,
    ) -> int:
        """Mark a completed cursor as fully published by the application flow."""
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE invoice_overview_checkpoints
                    SET checkpoint_status = 'finalized',
                        updated_at = ?
                    WHERE company_tax_code = ?
                      AND direction = ?
                      AND query_type = ?
                      AND from_date = ?
                      AND to_date = ?
                      AND status_filter = ?
                      AND checkpoint_status IN ('completed', 'completed_with_warning')
                    """,
                    (
                        timestamp, company_tax_code, direction, query_type,
                        from_date, to_date, status_filter,
                    ),
                )
                return cursor.rowcount

    def invalidate_overview_range(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> int:
        """Prevent a new job from resuming a cursor owned by an old job."""
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE invoice_overview_checkpoints
                    SET checkpoint_status = 'incomplete',
                        next_state = NULL
                    WHERE company_tax_code = ?
                      AND direction = ?
                      AND query_type = ?
                      AND from_date = ?
                      AND to_date = ?
                      AND checkpoint_status IN (
                          'in_progress', 'completed', 'completed_with_warning'
                      )
                    """,
                    (
                        company_tax_code, direction, query_type,
                        from_date, to_date,
                    ),
                )
                return cursor.rowcount

    def has_overview_refresh_run(self, refresh_run_id: str) -> bool:
        self.init_db()
        with closing(self._connect()) as connection:
            return connection.execute(
                'SELECT 1 FROM invoice_overview_refresh_items '
                'WHERE refresh_run_id=? LIMIT 1', (refresh_run_id,),
            ).fetchone() is not None

    @staticmethod
    def _upsert_rows(
        connection: sqlite3.Connection,
        rows: Sequence[tuple[Any, ...]],
    ) -> None:
        if not rows:
            return
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

    def verify_finalized_overview_range_from_database(
        self, *, company_tax_code: str, direction: str, query_type: str,
        from_date: str, to_date: str, status_filter: str,
        allow_source_mismatch: bool = False,
    ) -> bool:
        """Verify durable coverage without requiring retained raw artifacts."""
        checkpoint = self.get_overview_checkpoint(
            company_tax_code=company_tax_code, direction=direction,
            query_type=query_type, from_date=from_date, to_date=to_date,
            status_filter=status_filter,
        )
        if checkpoint is None or checkpoint['checkpoint_status'] != 'finalized':
            return False
        expected = checkpoint.get('expected_total')
        fetched = int(checkpoint.get('fetched_count') or 0)
        if (
            expected is not None
            and fetched != int(expected)
            and not allow_source_mismatch
        ):
            return False
        if fetched and int(checkpoint.get('page_number') or 0) < 1:
            return False
        return self.count_overview_items(
            company_tax_code, direction, query_type, from_date, to_date
        ) >= fetched

    @staticmethod
    def _upsert_checkpoint(
        connection: sqlite3.Connection, *, company_tax_code: str,
        direction: str, query_type: str, from_date: str, to_date: str,
        status_filter: str, next_state: str | None, page_number: int,
        fetched_count: int, expected_total: int | None, page_size: int,
        checkpoint_status: str, raw_json_path: Path | str, timestamp: str,
    ) -> None:
        connection.execute(
            """INSERT INTO invoice_overview_checkpoints (
                   company_tax_code, direction, query_type, from_date, to_date,
                   status_filter, next_state, page_number, fetched_count,
                   expected_total, last_page_size, checkpoint_status,
                   last_raw_page_path, error_message, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
               ON CONFLICT(company_tax_code, direction, query_type,
                           from_date, to_date, status_filter)
               DO UPDATE SET next_state=excluded.next_state,
                   page_number=excluded.page_number,
                   fetched_count=excluded.fetched_count,
                   expected_total=excluded.expected_total,
                   last_page_size=excluded.last_page_size,
                   checkpoint_status=excluded.checkpoint_status,
                   last_raw_page_path=excluded.last_raw_page_path,
                   error_message=NULL, updated_at=excluded.updated_at""",
            (
                company_tax_code, direction, query_type, from_date, to_date,
                status_filter, next_state, page_number, fetched_count,
                expected_total, page_size, checkpoint_status,
                str(raw_json_path), timestamp, timestamp,
            ),
        )

    @staticmethod
    def _replace_public_attributes(
        connection: sqlite3.Connection, company_tax_code: str,
        direction: str, query_type: str, items: Sequence[dict[str, Any]],
    ) -> None:
        for item in items:
            fields = item.get('_public_fields')
            if not isinstance(fields, dict):
                continue
            row = connection.execute(
                """SELECT id FROM invoice_overview_items
                   WHERE company_tax_code=? AND direction=? AND query_type=?
                     AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?""",
                (
                    company_tax_code, direction, query_type,
                    str(item['nbmst']), str(item['khhdon']), str(item['shdon']),
                    str(item['khmshdon']),
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError('overview item disappeared during attribute persistence')
            item_id = int(row[0])
            connection.execute(
                'DELETE FROM invoice_overview_attributes WHERE invoice_item_id=?',
                (item_id,),
            )
            connection.executemany(
                """INSERT INTO invoice_overview_attributes (
                       invoice_item_id, field_name, value_json
                   ) VALUES (?, ?, ?)""",
                [
                    (item_id, str(name), json.dumps(value, ensure_ascii=False))
                    for name, value in fields.items()
                ],
            )

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

    def iter_items_for_detail_task_generation(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        *,
        batch_size: int = 500,
        include_completed: bool = False,
    ):
        """Yield pending detail keys in bounded batches using an ID cursor."""
        self._validate_date_range(from_date, to_date)
        if batch_size < 1:
            raise ValueError('batch_size must be positive')
        self.init_db()
        last_id = 0
        while True:
            completed_filter = '' if include_completed else (
                'AND (detail_fetched = 0 OR detail_path IS NULL)'
            )
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"""
                    SELECT id, company_tax_code, direction, query_type,
                           invoice_category, nbmst, khhdon, shdon, khmshdon,
                           nlap, nlap_date
                    FROM invoice_overview_items
                    WHERE id > ?
                      AND company_tax_code = ?
                      AND direction = ?
                      AND query_type = ?
                      AND nlap_date BETWEEN ? AND ?
                      {completed_filter}
                    ORDER BY id
                    LIMIT ?
                    """,
                    (
                        last_id, company_tax_code, direction, query_type,
                        from_date, to_date, batch_size,
                    ),
                ).fetchall()
            if not rows:
                return
            batch = [dict(row) for row in rows]
            yield batch
            last_id = int(rows[-1]['id'])

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
    def _normalized_item_date(item: dict[str, Any]) -> str | None:
        normalized = normalize_business_date(item.get('nlap'))
        return normalized or normalize_business_date(item.get('nlap_date'))

    @staticmethod
    def _backfill_missing_dates(
        connection: sqlite3.Connection, *, batch_size: int = 500,
    ) -> None:
        """Compatibility-only bounded backfill for rows with no date at all."""
        rows = connection.execute(
            """SELECT id, nlap FROM invoice_overview_items
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
                'UPDATE invoice_overview_items SET nlap_date = ? WHERE id = ?',
                updates,
            )

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
