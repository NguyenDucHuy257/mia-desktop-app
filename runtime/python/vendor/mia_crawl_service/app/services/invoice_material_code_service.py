from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.config.crawl_config import VALID_DIRECTIONS, VALID_QUERY_TYPES
from app.matchers.material_code_matcher import (
    DetailInvoiceLine,
    extract_detail_lines,
    match_material_codes,
    unmatched_material_codes,
)
from app.parsers.invoice_xml_parser import parse_invoice_xml
from app.repositories.invoice_material_code_repository import (
    InvoiceMaterialCodeRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MvtPreflightReport:
    company_tax_code: str
    direction: str
    query_type: str
    from_date: str
    to_date: str
    total_detail_invoices: int
    xml_packages_ready: int
    missing_xml_packages: int
    package_unavailable: int
    missing_xml_files: int
    unreadable_xml_files: int

    @property
    def can_proceed(self) -> bool:
        return (
            self.missing_xml_packages == 0
            and self.missing_xml_files == 0
            and self.unreadable_xml_files == 0
        )


class MvtPreflightError(RuntimeError):
    def __init__(self, report: MvtPreflightReport) -> None:
        super().__init__('XML package preflight failed')
        self.report = report


class MvtDetailDataError(RuntimeError):
    pass


class InvoiceMaterialCodeService:
    """Extract MVT from local XML packages and persist it into detail rows."""

    def __init__(
        self,
        database_path: Path | str,
        repository: InvoiceMaterialCodeRepository | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.repository = repository or InvoiceMaterialCodeRepository(database_path)

    def extract_material_codes(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        only_pending: bool = True,
        overwrite: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        self._validate_request(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
        self.repository.migrate()
        records = self.repository.get_scope_records(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
        )

        detail_payloads = self._load_all_detail_payloads(records)
        report, package_states = self._preflight(
            records,
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
        )
        if not report.can_proceed:
            raise MvtPreflightError(report)

        already_completed = sum(
            not self._is_pending(record.get('material_codes_json'))
            for record in records
        )
        if overwrite:
            selected = list(records)
        else:
            # SQL performs the mandated pending filter. Even when
            # only_pending=False, an existing result remains protected unless
            # overwrite=True is explicit.
            selected = self.repository.get_scope_records(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                pending_only=True,
            )
        if limit is not None:
            selected = selected[:limit]

        processed_count = 0
        failed_count = 0
        matched_count = 0
        unmatched_count = 0
        ambiguous_count = 0
        without_material_code_count = 0

        with self.repository.transaction() as connection:
            for record in selected:
                key = self._record_key(record)
                detail_lines: list[DetailInvoiceLine] = []
                xml_line_count = 0
                connection.execute('SAVEPOINT mvt_invoice')
                try:
                    detail_lines = extract_detail_lines(detail_payloads[record['id']])
                    if package_states[record['id']] == 'unavailable':
                        material_codes = unmatched_material_codes(detail_lines)
                        status = 'package_unavailable'
                    else:
                        xml_lines = parse_invoice_xml(Path(record['xml_path']))
                        xml_line_count = len(xml_lines)
                        material_codes = match_material_codes(detail_lines, xml_lines)
                        status = 'processed'
                    serialized = json.dumps(
                        material_codes,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    )
                    counts = self._result_counts(material_codes)
                    self.repository.update_material_codes(
                        connection, record, serialized
                    )
                    connection.execute('RELEASE SAVEPOINT mvt_invoice')
                    processed_count += 1
                    matched_count += counts['matched']
                    unmatched_count += counts['unmatched']
                    ambiguous_count += counts['ambiguous']
                    without_material_code_count += counts['without_material_code']
                    log_error = (
                        self._single_line(record.get('unavailable_reason'))
                        if status == 'package_unavailable'
                        else 'none'
                    )
                    logger.info(
                        'MVT invoice direction=%s query_type=%s nbmst=%s '
                        'khhdon=%s shdon=%s khmshdon=%s detail_path=%s '
                        'xml_path=%s detail_line_count=%d xml_line_count=%d '
                        'matched_count=%d unmatched_count=%d ambiguous_count=%d '
                        'status=%s error=%s',
                        record['direction'], record['query_type'], record['nbmst'],
                        record['khhdon'], record['shdon'], record['khmshdon'],
                        record['raw_detail_path'], record.get('xml_path') or '',
                        len(detail_lines), xml_line_count, counts['matched'],
                        counts['unmatched'], counts['ambiguous'], status,
                        log_error,
                    )
                except Exception as error:
                    connection.execute('ROLLBACK TO SAVEPOINT mvt_invoice')
                    connection.execute('RELEASE SAVEPOINT mvt_invoice')
                    failed_count += 1
                    logger.exception(
                        'MVT invoice direction=%s query_type=%s nbmst=%s '
                        'khhdon=%s shdon=%s khmshdon=%s detail_path=%s '
                        'xml_path=%s detail_line_count=%d xml_line_count=%d '
                        'matched_count=0 unmatched_count=0 ambiguous_count=0 '
                        'status=failed error=%s: %s',
                        record['direction'], record['query_type'], record['nbmst'],
                        record['khhdon'], record['shdon'], record['khmshdon'],
                        record['raw_detail_path'], record.get('xml_path') or '',
                        len(detail_lines), xml_line_count,
                        type(error).__name__, error,
                    )
                    logger.debug('MVT failed invoice key=%s', key)

        return {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'from_date': from_date,
            'to_date': to_date,
            'invoices_in_detail_db': len(records),
            'xml_packages_ready': report.xml_packages_ready,
            'package_unavailable': report.package_unavailable,
            'invoices_processed': processed_count,
            'already_completed': already_completed,
            'failed': failed_count,
            'matched': matched_count,
            'unmatched': unmatched_count,
            'ambiguous': ambiguous_count,
            'without_mhhdvu': without_material_code_count,
            'is_complete': failed_count == 0,
        }

    @staticmethod
    def _load_all_detail_payloads(
        records: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        payloads: dict[int, dict[str, Any]] = {}
        errors: list[str] = []
        for record in records:
            path = Path(str(record['raw_detail_path']))
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('JSON root is not an object')
                # Validate hdhhdvu before package preflight or database writes.
                extract_detail_lines(payload)
                payloads[record['id']] = payload
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                errors.append(
                    f"{record['nbmst']}/{record['khhdon']}/{record['shdon']}/"
                    f"{record['khmshdon']}: {type(error).__name__}: {error}"
                )
        if errors:
            raise MvtDetailDataError(
                'Invalid raw detail JSON in selected scope:\n' + '\n'.join(errors)
            )
        return payloads

    @staticmethod
    def _preflight(
        records: list[dict[str, Any]],
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> tuple[MvtPreflightReport, dict[int, str]]:
        ready = missing = unavailable = missing_file = unreadable = 0
        states: dict[int, str] = {}
        for record in records:
            if int(record.get('package_unavailable') or 0) == 1:
                unavailable += 1
                states[record['id']] = 'unavailable'
                continue
            if (
                record.get('package_id') is None
                or int(record.get('xml_fetched') or 0) != 1
                or not str(record.get('xml_path') or '').strip()
                or str(record.get('package_error_message') or '').strip()
            ):
                missing += 1
                states[record['id']] = 'missing'
                continue
            xml_path = Path(str(record['xml_path']))
            if not xml_path.is_file():
                missing_file += 1
                states[record['id']] = 'missing_file'
                continue
            try:
                with xml_path.open('rb') as stream:
                    stream.read(1)
            except OSError:
                unreadable += 1
                states[record['id']] = 'unreadable'
                continue
            ready += 1
            states[record['id']] = 'ready'
        return (
            MvtPreflightReport(
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=from_date,
                to_date=to_date,
                total_detail_invoices=len(records),
                xml_packages_ready=ready,
                missing_xml_packages=missing,
                package_unavailable=unavailable,
                missing_xml_files=missing_file,
                unreadable_xml_files=unreadable,
            ),
            states,
        )

    @staticmethod
    def _result_counts(results: list[dict[str, Any]]) -> dict[str, int]:
        return {
            'matched': sum(item['match_status'] == 'matched' for item in results),
            'unmatched': sum(item['match_status'] == 'unmatched' for item in results),
            'ambiguous': sum(item['match_status'] == 'ambiguous' for item in results),
            'without_material_code': sum(
                item['match_status'] == 'matched'
                and item.get('material_code') is None
                for item in results
            ),
        }

    @staticmethod
    def _is_pending(value: Any) -> bool:
        return value is None or not str(value).strip()

    @staticmethod
    def _record_key(record: dict[str, Any]) -> str:
        return '/'.join(
            str(record[field])
            for field in ('nbmst', 'khhdon', 'shdon', 'khmshdon')
        )

    @staticmethod
    def _single_line(value: Any) -> str:
        return ' '.join(str(value or 'none').split())

    @staticmethod
    def _validate_request(
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        limit: int | None,
    ) -> None:
        if not company_tax_code.strip():
            raise ValueError('company_tax_code must not be empty')
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        if query_type not in VALID_QUERY_TYPES:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        ):
            raise ValueError('limit must be a positive integer or None')
