from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class InvoicePackageRepository:
    """Persist package state and select package keys from the local invoice DB."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS invoice_package_items (
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
                        package_dir TEXT,
                        raw_zip_path TEXT,
                        xml_path TEXT,
                        html_path TEXT,
                        xml_fetched INTEGER NOT NULL DEFAULT 0,
                        html_fetched INTEGER NOT NULL DEFAULT 0,
                        fetched_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        error_message TEXT,
                        unavailable INTEGER NOT NULL DEFAULT 0,
                        unavailable_reason TEXT,
                        UNIQUE (
                            company_tax_code, direction, query_type,
                            nbmst, khhdon, shdon, khmshdon
                        )
                    );

                    CREATE INDEX IF NOT EXISTS idx_invoice_package_range
                    ON invoice_package_items (
                        company_tax_code, direction, query_type, nlap_date
                    );

                    CREATE INDEX IF NOT EXISTS idx_invoice_package_pending
                    ON invoice_package_items (
                        company_tax_code, direction, query_type,
                        xml_fetched, html_fetched
                    );
                    """
                )
                columns = {
                    row['name']
                    for row in connection.execute(
                        'PRAGMA table_info(invoice_package_items)'
                    ).fetchall()
                }
                if 'unavailable' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_package_items '
                        'ADD COLUMN unavailable INTEGER NOT NULL DEFAULT 0'
                    )
                if 'unavailable_reason' not in columns:
                    connection.execute(
                        'ALTER TABLE invoice_package_items '
                        'ADD COLUMN unavailable_reason TEXT'
                    )
                # Backfill permanent errors recorded before the status column
                # existed so old rows stop cycling through every pending run.
                connection.execute(
                    """
                    UPDATE invoice_package_items
                    SET unavailable = 1,
                        unavailable_reason = error_message
                    WHERE unavailable = 0
                      AND error_message LIKE ?
                      AND (xml_fetched = 0 OR html_fetched = 0)
                    """,
                    ('%Không tồn tại hồ sơ gốc của hóa đơn.%',),
                )

    def upsert_package_success(
        self,
        *,
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
        package_dir: Path | str,
        raw_zip_path: Path | str,
        xml_path: Path | str | None,
        html_path: Path | str | None,
        xml_fetched: bool,
        html_fetched: bool,
        fetched_at: str | None = None,
    ) -> None:
        timestamp = fetched_at or datetime.now(timezone.utc).isoformat()
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO invoice_package_items (
                        company_tax_code, direction, query_type, invoice_category,
                        nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                        package_dir, raw_zip_path, xml_path, html_path,
                        xml_fetched, html_fetched, fetched_at, created_at,
                        updated_at, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT (
                        company_tax_code, direction, query_type,
                        nbmst, khhdon, shdon, khmshdon
                    ) DO UPDATE SET
                        invoice_category = excluded.invoice_category,
                        nlap = excluded.nlap,
                        nlap_date = excluded.nlap_date,
                        package_dir = excluded.package_dir,
                        raw_zip_path = excluded.raw_zip_path,
                        xml_path = COALESCE(excluded.xml_path, invoice_package_items.xml_path),
                        html_path = COALESCE(excluded.html_path, invoice_package_items.html_path),
                        xml_fetched = MAX(invoice_package_items.xml_fetched, excluded.xml_fetched),
                        html_fetched = MAX(invoice_package_items.html_fetched, excluded.html_fetched),
                        fetched_at = excluded.fetched_at,
                        updated_at = excluded.updated_at,
                        error_message = NULL,
                        unavailable = 0,
                        unavailable_reason = NULL
                    """,
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon),
                        nlap, nlap_date, str(package_dir), str(raw_zip_path),
                        str(xml_path) if xml_path is not None else None,
                        str(html_path) if html_path is not None else None,
                        int(xml_fetched), int(html_fetched), timestamp, timestamp,
                        timestamp,
                    ),
                )

    def upsert_package_error(
        self,
        *,
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
        error_message: str,
        attempted_at: str | None = None,
        unavailable: bool = False,
    ) -> None:
        timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
        self.init_db()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO invoice_package_items (
                        company_tax_code, direction, query_type, invoice_category,
                        nbmst, khhdon, shdon, khmshdon, nlap, nlap_date,
                        xml_fetched, html_fetched, created_at, updated_at,
                        error_message, unavailable, unavailable_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
                    ON CONFLICT (
                        company_tax_code, direction, query_type,
                        nbmst, khhdon, shdon, khmshdon
                    ) DO UPDATE SET
                        invoice_category = excluded.invoice_category,
                        nlap = excluded.nlap,
                        nlap_date = excluded.nlap_date,
                        updated_at = excluded.updated_at,
                        error_message = excluded.error_message,
                        unavailable = excluded.unavailable,
                        unavailable_reason = excluded.unavailable_reason
                    """,
                    (
                        company_tax_code, direction, query_type, invoice_category,
                        str(nbmst), str(khhdon), str(shdon), str(khmshdon),
                        nlap, nlap_date, timestamp, timestamp,
                        error_message[:2000],
                        int(unavailable),
                        error_message[:2000] if unavailable else None,
                    ),
                )

    def get_package_by_invoice_key(
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
                SELECT * FROM invoice_package_items
                WHERE company_tax_code = ? AND direction = ? AND query_type = ?
                  AND nbmst = ? AND khhdon = ? AND shdon = ? AND khmshdon = ?
                """,
                (
                    company_tax_code, direction, query_type, str(nbmst),
                    str(khhdon), str(shdon), str(khmshdon),
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_invoice_keys_for_package_download(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
        only_pending: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_selection(from_date, to_date, export_xml, export_html, limit)
        self.init_db()
        with closing(self._connect()) as connection:
            sources: list[str] = []
            if self._table_exists(connection, 'invoice_detail_items'):
                sources.append(
                    """
                    SELECT 0 AS source_priority, id AS source_id, company_tax_code,
                           direction, query_type, invoice_category, nbmst, khhdon,
                           shdon, khmshdon, nlap, nlap_date
                    FROM invoice_detail_items
                    """
                )
            if self._table_exists(connection, 'invoice_overview_items'):
                sources.append(
                    """
                    SELECT 1 AS source_priority, id AS source_id, company_tax_code,
                           direction, query_type, invoice_category, nbmst, khhdon,
                           shdon, khmshdon, nlap, nlap_date
                    FROM invoice_overview_items
                    """
                )
            if not sources:
                return []

            pending_parts: list[str] = []
            if export_xml:
                pending_parts.append('(p.xml_fetched IS NOT 1 OR p.xml_path IS NULL)')
            if export_html:
                pending_parts.append('(p.html_fetched IS NOT 1 OR p.html_path IS NULL)')
            pending_sql = ''
            if only_pending:
                pending_sql = (
                    ' AND COALESCE(p.unavailable, 0) = 0 AND ('
                    + ' OR '.join(pending_parts)
                    + ')'
                )

            # ROW_NUMBER makes detail rows authoritative when the same invoice
            # key is available from both local pipelines.
            sql = f"""
                WITH all_sources AS ({' UNION ALL '.join(sources)}),
                ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY company_tax_code, direction, query_type,
                                     nbmst, khhdon, shdon, khmshdon
                        ORDER BY source_priority, source_id
                    ) AS rank_number
                    FROM all_sources
                )
                SELECT s.company_tax_code, s.direction, s.query_type,
                       s.invoice_category, s.nbmst, s.khhdon, s.shdon,
                       s.khmshdon, s.nlap, s.nlap_date
                FROM ranked AS s
                LEFT JOIN invoice_package_items AS p
                  ON p.company_tax_code = s.company_tax_code
                 AND p.direction = s.direction
                 AND p.query_type = s.query_type
                 AND p.nbmst = s.nbmst
                 AND p.khhdon = s.khhdon
                 AND p.shdon = s.shdon
                 AND p.khmshdon = s.khmshdon
                WHERE s.rank_number = 1
                  AND s.company_tax_code = ?
                  AND s.direction = ?
                  AND s.query_type = ?
                  AND s.nlap_date BETWEEN ? AND ?
                  {pending_sql}
                ORDER BY s.nlap_date, s.source_id
            """
            parameters: list[Any] = [
                company_tax_code, direction, query_type, from_date, to_date,
            ]
            if limit is not None:
                sql += ' LIMIT ?'
                parameters.append(limit)
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def count_unavailable_packages(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> int:
        """Count auditable packages that the portal says do not exist."""
        self.init_db()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS unavailable_count
                FROM invoice_package_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nlap_date BETWEEN ? AND ?
                  AND unavailable = 1
                """,
                (
                    company_tax_code, direction, query_type,
                    from_date, to_date,
                ),
            ).fetchone()
        return int(row['unavailable_count'])

    def reconcile_missing_package_files(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        *,
        export_xml: bool,
        export_html: bool,
    ) -> int:
        """Requeue successful DB rows whose requested files disappeared."""
        self.init_db()
        reconciled_count = 0
        with closing(self._connect()) as connection:
            with connection:
                rows = connection.execute(
                    """
                    SELECT id, raw_zip_path, xml_path, html_path,
                           xml_fetched, html_fetched
                    FROM invoice_package_items
                    WHERE company_tax_code = ?
                      AND direction = ?
                      AND query_type = ?
                      AND nlap_date BETWEEN ? AND ?
                      AND unavailable = 0
                    """,
                    (
                        company_tax_code, direction, query_type,
                        from_date, to_date,
                    ),
                ).fetchall()
                for row in rows:
                    raw_missing = not row['raw_zip_path'] or not Path(
                        row['raw_zip_path']
                    ).is_file()
                    xml_missing = export_xml and (
                        row['xml_fetched'] != 1
                        or not row['xml_path']
                        or not Path(row['xml_path']).is_file()
                    )
                    html_missing = export_html and (
                        row['html_fetched'] != 1
                        or not row['html_path']
                        or not Path(row['html_path']).is_file()
                    )
                    if not raw_missing and not xml_missing and not html_missing:
                        continue
                    reset_xml = export_xml and (raw_missing or xml_missing)
                    reset_html = export_html and (raw_missing or html_missing)
                    connection.execute(
                        """
                        UPDATE invoice_package_items
                        SET raw_zip_path = CASE WHEN ? THEN NULL ELSE raw_zip_path END,
                            xml_fetched = CASE WHEN ? THEN 0 ELSE xml_fetched END,
                            xml_path = CASE WHEN ? THEN NULL ELSE xml_path END,
                            html_fetched = CASE WHEN ? THEN 0 ELSE html_fetched END,
                            html_path = CASE WHEN ? THEN NULL ELSE html_path END,
                            error_message = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(raw_missing), int(reset_xml), int(reset_xml),
                            int(reset_html), int(reset_html),
                            'Local package files are missing; queued for re-download',
                            datetime.now(timezone.utc).isoformat(), row['id'],
                        ),
                    )
                    reconciled_count += 1
        return reconciled_count

    def count_verified_package_files(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        *,
        export_xml: bool,
        export_html: bool,
    ) -> int:
        """Count package rows whose requested files really exist on disk."""
        self.init_db()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT raw_zip_path, xml_path, html_path,
                       xml_fetched, html_fetched
                FROM invoice_package_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nlap_date BETWEEN ? AND ?
                  AND unavailable = 0
                """,
                (
                    company_tax_code, direction, query_type,
                    from_date, to_date,
                ),
            ).fetchall()
        verified_count = 0
        for row in rows:
            if not row['raw_zip_path'] or not Path(row['raw_zip_path']).is_file():
                continue
            if export_xml and (
                row['xml_fetched'] != 1
                or not row['xml_path']
                or not Path(row['xml_path']).is_file()
            ):
                continue
            if export_html and (
                row['html_fetched'] != 1
                or not row['html_path']
                or not Path(row['html_path']).is_file()
            ):
                continue
            verified_count += 1
        return verified_count

    def get_html_items_for_pdf_export(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Select successfully fetched invoice HTML rows for PDF conversion."""
        self._validate_selection(
            from_date, to_date, export_xml=False, export_html=True, limit=limit
        )
        self.init_db()
        with closing(self._connect()) as connection:
            sql = """
                SELECT nbmst, khhdon, shdon, khmshdon, nlap, nlap_date, html_path
                FROM invoice_package_items
                WHERE company_tax_code = ?
                  AND direction = ?
                  AND query_type = ?
                  AND nlap_date BETWEEN ? AND ?
                  AND html_fetched = 1
                  AND html_path IS NOT NULL
                  AND html_path <> ''
                  AND unavailable = 0
                ORDER BY nlap_date, id
            """
            parameters: list[Any] = [
                company_tax_code, direction, query_type, from_date, to_date,
            ]
            if limit is not None:
                sql += ' LIMIT ?'
                parameters.append(limit)
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_items_for_package_download(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
        only_pending: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility alias for package input selection."""
        return self.get_invoice_keys_for_package_download(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            export_xml=export_xml,
            export_html=export_html,
            only_pending=only_pending,
            limit=limit,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone() is not None

    @staticmethod
    def _validate_selection(
        from_date: str,
        to_date: str,
        export_xml: bool,
        export_html: bool,
        limit: int | None,
    ) -> None:
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        if not export_xml and not export_html:
            raise ValueError('At least one of export_xml/export_html must be True')
        if limit is not None and limit <= 0:
            raise ValueError('limit must be greater than zero')
