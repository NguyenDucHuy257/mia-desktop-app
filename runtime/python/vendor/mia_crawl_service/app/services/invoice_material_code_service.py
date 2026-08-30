from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.job_engine.interruptions import PipelineInterruption

from app.config.crawl_config import VALID_DIRECTIONS, VALID_QUERY_TYPES
from app.matchers.material_code_matcher import (
    DetailInvoiceLine,
    extract_detail_lines,
    match_material_codes,
    unmatched_material_codes,
)
from app.parsers.invoice_xml_parser import (
    InvoiceXmlMalformedError,
    InvoiceXmlTooLargeError,
    XmlInvoiceLine,
    parse_invoice_xml,
    parse_decimal,
)
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
    malformed_xml_files: int

    @property
    def can_proceed(self) -> bool:
        return (
            self.missing_xml_packages == 0
            and self.missing_xml_files == 0
            and self.unreadable_xml_files == 0
            and self.malformed_xml_files == 0
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
        progress_callback=None,
        interruption_check=None,
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

        detail_lines_by_invoice = self._load_all_detail_lines(records)
        report, package_states, xml_payloads = self._preflight(
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

        prepared: list[dict[str, Any]] = []
        for record in selected:
            try:
                if interruption_check:
                    interruption_check()
                self._progress(progress_callback, 'detail_record_loaded', record)
                self._progress(progress_callback, 'raw_detail_loaded', record)
                self._progress(progress_callback, 'xml_record_loaded', record)
                if package_states[record['id']] != 'unavailable':
                    self._progress(progress_callback, 'xml_loaded', record)
                detail_lines = detail_lines_by_invoice[record['id']]
                self._progress(progress_callback, 'detail_lines_parsed', record)
                if package_states[record['id']] == 'unavailable':
                    material_codes = unmatched_material_codes(detail_lines)
                    status = 'package_unavailable'
                    xml_line_count = 0
                else:
                    xml_lines = xml_payloads[record['id']]
                    self._progress(progress_callback, 'xml_lines_parsed', record)
                    xml_line_count = len(xml_lines)
                    material_codes = match_material_codes(detail_lines, xml_lines)
                    status = 'processed'
                self._progress(progress_callback, 'matching_completed', record)
                self._progress(
                    progress_callback, 'matching_result_validated', record,
                    self._result_counts(material_codes),
                )
                serialized = json.dumps(
                    material_codes, ensure_ascii=False, separators=(',', ':')
                )
                self._progress(progress_callback, 'material_codes_serialized', record)
                prepared.append({
                    'record': record,
                    'serialized': serialized,
                    'counts': self._result_counts(material_codes),
                    'detail_line_count': len(detail_lines),
                    'xml_line_count': xml_line_count,
                    'status': status,
                })
            except PipelineInterruption:
                raise
            except Exception as error:
                failed_count += 1
                self._progress(progress_callback, 'failed', record)
                logger.error(
                    'MVT preparation failed direction=%s query_type=%s '
                    'error_type=%s', record['direction'], record['query_type'],
                    type(error).__name__,
                )

        for item in prepared:
            record = item['record']
            counts = item['counts']
            try:
                if interruption_check:
                    interruption_check()
                # Keep the SQLite write lock to one atomic invoice update.
                with self.repository.transaction() as connection:
                    self.repository.update_material_codes(
                        connection, record, item['serialized']
                    )
                self._progress(
                    progress_callback, 'material_codes_database_committed', record,
                    counts,
                )
                if self.repository.get_material_codes_json(record) != item['serialized']:
                    raise RuntimeError('Persisted material codes verification failed')
                self._progress(
                    progress_callback, 'persisted_result_verified', record, counts
                )
                self._progress(progress_callback, 'completed', record, counts)
            except PipelineInterruption:
                raise
            except Exception as error:
                failed_count += 1
                self._progress(progress_callback, 'failed', record)
                logger.error(
                    'MVT update failed direction=%s query_type=%s error_type=%s',
                    record['direction'], record['query_type'], type(error).__name__,
                )
                continue
            processed_count += 1
            matched_count += counts['matched']
            unmatched_count += counts['unmatched']
            ambiguous_count += counts['ambiguous']
            without_material_code_count += counts['without_material_code']
            logger.info(
                'MVT invoice direction=%s query_type=%s detail_line_count=%d '
                'xml_line_count=%d matched_count=%d unmatched_count=%d '
                'ambiguous_count=%d status=%s',
                record['direction'], record['query_type'], item['detail_line_count'],
                item['xml_line_count'], counts['matched'], counts['unmatched'],
                counts['ambiguous'], item['status'],
            )

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
    def _progress(callback, event, record, counters=None):
        if callback is not None:
            callback(event, record, counters or {})

    def _load_all_detail_lines(
        self, records: list[dict[str, Any]],
    ) -> dict[int, list[DetailInvoiceLine]]:
        parsed_lines: dict[int, list[DetailInvoiceLine]] = {}
        errors: list[str] = []
        for record in records:
            try:
                if record.get('normalized_ready'):
                    rows = self.repository.get_normalized_detail_lines(record['id'])
                    parsed_lines[record['id']] = [
                        DetailInvoiceLine(
                            index=index,
                            product_name=str(row.get('ten') or '').strip(),
                            unit=(str(row['dvtinh']) if row.get('dvtinh') not in (None, '') else None),
                            quantity=parse_decimal(row.get('sluong')),
                            unit_price=parse_decimal(row.get('dgia')),
                            amount=parse_decimal(row.get('thtien')),
                        )
                        for index, row in enumerate(rows)
                    ]
                    continue
                path = Path(str(record['raw_detail_path']))
                payload = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('JSON root is not an object')
                parsed_lines[record['id']] = extract_detail_lines(payload)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                errors.append(
                    f"{record['nbmst']}/{record['khhdon']}/{record['shdon']}/"
                    f"{record['khmshdon']}: {type(error).__name__}: {error}"
                )
        if errors:
            raise MvtDetailDataError(
                'Invalid raw detail JSON in selected scope:\n' + '\n'.join(errors)
            )
        return parsed_lines

    @staticmethod
    def _preflight(
        records: list[dict[str, Any]],
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
    ) -> tuple[
        MvtPreflightReport, dict[int, str], dict[int, list[XmlInvoiceLine]]
    ]:
        ready = missing = unavailable = missing_file = unreadable = malformed = 0
        states: dict[int, str] = {}
        xml_payloads: dict[int, list[XmlInvoiceLine]] = {}
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
                xml_payloads[record['id']] = parse_invoice_xml(xml_path)
            except (InvoiceXmlMalformedError, InvoiceXmlTooLargeError):
                malformed += 1
                states[record['id']] = 'malformed'
                continue
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
                malformed_xml_files=malformed,
            ),
            states,
            xml_payloads,
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
