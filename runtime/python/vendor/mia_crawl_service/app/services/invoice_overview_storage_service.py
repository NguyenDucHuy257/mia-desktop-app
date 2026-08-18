from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY, VALID_DIRECTIONS
from app.crawlers.endpoints import invoice_list_url
from app.crawlers.invoice_crawler import InvoiceFetchResult
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository

logger = logging.getLogger(__name__)

REQUIRED_ITEM_FIELDS = ('nbmst', 'khhdon', 'shdon', 'khmshdon')


class InvoiceOverviewStorageService:
    """Store complete list responses as JSON and index their invoice keys in SQLite."""

    def __init__(self, data_root: Path | str = Path('data')) -> None:
        self.data_root = Path(data_root)

    def save_invoice_overview_batch(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        api_responses: Sequence[dict[str, Any]],
        final_status: str = 'completed',
        checkpoint_state: str | None = None,
    ) -> dict[str, Any]:
        company_tax_code = self._validate_company_tax_code(company_tax_code)
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        if query_type not in QUERY_TYPE_TO_CATEGORY:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        self._validate_date_range(from_date, to_date)

        logger.info(
            'Saving invoice overview batch company=%s direction=%s query_type=%s range=%s..%s',
            company_tax_code,
            direction,
            query_type,
            from_date,
            to_date,
        )

        all_datas, total = self._merge_responses(api_responses)
        created_at = datetime.now(timezone.utc).isoformat()
        invoice_category = QUERY_TYPE_TO_CATEGORY[query_type]
        company_root = self.data_root / company_tax_code
        raw_json_path = (
            company_root
            / 'raw'
            / 'invoice_lists'
            / direction
            / query_type
            / f'{from_date}_{to_date}.json'
        )
        raw_document = {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'from_date': from_date,
            'to_date': to_date,
            'total': total,
            'fetched_count': len(all_datas),
            'final_status': final_status,
            'checkpoint_state': checkpoint_state,
            'created_at': created_at,
            'datas': all_datas,
        }
        self._write_json_atomically(raw_json_path, raw_document)
        logger.info(
            'Saved raw invoice overview JSON path=%s fetched_count=%d total=%s',
            raw_json_path,
            len(all_datas),
            total,
        )

        normalized_items = self._normalize_valid_items(all_datas)
        database_path = company_root / 'db' / 'invoices.sqlite3'
        repository = InvoiceOverviewRepository(database_path)
        upserted_count = repository.upsert_items(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            invoice_category=invoice_category,
            raw_json_path=raw_json_path.resolve(),
            items=normalized_items,
            timestamp=created_at,
        )
        logger.info(
            'Upserted invoice overview SQLite path=%s upserted_count=%d skipped_count=%d',
            database_path,
            upserted_count,
            len(all_datas) - upserted_count,
        )

        return {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'invoice_category': invoice_category,
            'raw_json_path': str(raw_json_path.resolve()),
            'fetched_count': len(all_datas),
            'upserted_count': upserted_count,
            'final_status': final_status,
            'checkpoint_state': checkpoint_state,
        }

    def write_overview_audit_report(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        profile: str,
        results: Sequence[InvoiceFetchResult],
        report_dir_name: str = 'overview_audit',
    ) -> Path:
        """Write one deterministic, non-secret TXT audit for a logical date range."""
        company_tax_code = self._validate_company_tax_code(company_tax_code)
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        if query_type not in QUERY_TYPE_TO_CATEGORY:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        self._validate_date_range(from_date, to_date)
        if not results:
            raise ValueError('results must not be empty')
        if not report_dir_name.strip() or any(part in report_dir_name for part in ('/', '\\')):
            raise ValueError('report_dir_name must be a safe directory name')

        category = QUERY_TYPE_TO_CATEGORY[query_type]
        expected_values = [item.first_page_total for item in results]
        expected_total = (
            sum(value for value in expected_values if value is not None)
            if all(value is not None for value in expected_values)
            else None
        )
        fetched_count = sum(len(item.records) for item in results)
        missing_count = (
            max(expected_total - fetched_count, 0) if expected_total is not None else None
        )
        estimated_pages = sum(item.estimated_pages for item in results)
        pages_loaded = sum(item.pages for item in results)
        range_rank = {'light': 0, 'heavy': 1, 'extreme': 2}
        range_class = max(
            (item.range_class for item in results), key=lambda item: range_rank[item]
        )
        final_status = self._aggregate_status(results)
        last_result = results[-1]
        checkpoint_state = next(
            (item.checkpoint_state for item in reversed(results) if item.checkpoint_state),
            None,
        )
        action = {
            'completed': 'none',
            'completed_with_warning': 'review_suspicious_pages',
            'incomplete': 'retry_range_from_checkpoint_or_full_range',
            'rate_limited': 'resume_after_cooldown',
            'timeout_failed': 'retry_range_later',
            'partial_saved': 'resume_or_retry_range',
        }[final_status]

        lines = [
            'OVERVIEW AUDIT REPORT', '',
            f'company_tax_code: {company_tax_code}',
            f'direction: {direction}',
            f'query_type: {query_type}',
            f'category: {category}',
            f'from_date: {from_date}',
            f'to_date: {to_date}',
            f'endpoint: {invoice_list_url(category, direction)}',
            f'profile: {profile}', '',
            f'total_from_first_page: {self._audit_value(expected_total)}',
            f'fetched_count: {fetched_count}',
            f'missing_count: {self._audit_value(missing_count)}',
            f'estimated_pages: {estimated_pages}',
            f'pages_loaded: {pages_loaded}',
            f'range_class: {range_class}',
            f'retry_count: {sum(item.retry_count for item in results)}',
            f'timeout_count: {sum(item.timeout_count for item in results)}',
            f'consecutive_429_count: {max(item.consecutive_429_count for item in results)}',
            f'final_status: {final_status}',
            f'final_state: {self._audit_value(last_result.final_state)}',
            f'last_page_size: {last_result.final_page_size}', '',
            'PAGE DETAILS', '',
            'page | size | expected_received | received | fetched_before | fetched_after | total | state | http_status | retry_count | timeout_count | note',
        ]
        page_offset = 0
        fetched_offset = 0
        for result in results:
            for detail in result.page_details:
                lines.append(
                    f'{page_offset + detail.page} | {detail.size} | '
                    f'{self._audit_value(detail.expected_received)} | {detail.received} | '
                    f'{fetched_offset + detail.fetched_before} | '
                    f'{fetched_offset + detail.fetched_after} | '
                    f'{self._audit_value(detail.total)} | '
                    f'{self._audit_value(detail.state)} | {detail.http_status} | '
                    f'{detail.retry_count} | {detail.timeout_count} | {detail.note}'
                )
            page_offset += max(
                result.pages,
                max((detail.page for detail in result.page_details), default=0),
            )
            fetched_offset += len(result.records)

        lines.extend(['', 'SUSPICIOUS PAGES', '',
                      'page | reason | expected_received | received | http_status | note'])
        page_offset = 0
        for result in results:
            for suspicious in result.suspicious_pages:
                lines.append(
                    f'{page_offset + suspicious.page} | {suspicious.reason} | '
                    f'{self._audit_value(suspicious.expected_received)} | '
                    f'{suspicious.received} | '
                    f'{self._audit_value(suspicious.http_status)} | {suspicious.note}'
                )
            page_offset += max(
                result.pages,
                max((detail.page for detail in result.page_details), default=0),
            )

        lines.extend([
            '', 'SUMMARY', '',
            f'expected_total: {self._audit_value(expected_total)}',
            f'actual_fetched: {fetched_count}',
            f'missing: {self._audit_value(missing_count)}',
            f'action: {action}',
            f'checkpoint_state: {self._audit_value(checkpoint_state)}',
            f'resume_recommended: {str(final_status in {"incomplete", "rate_limited", "timeout_failed", "partial_saved"}).lower()}',
            '',
        ])
        report_path = (
            self.data_root / company_tax_code / 'reports' / report_dir_name
            / f'{direction}_{query_type}_{from_date}_{to_date}.txt'
        )
        self._write_text_atomically(report_path, '\n'.join(lines))
        return report_path.resolve()

    @staticmethod
    def _aggregate_status(results: Sequence[InvoiceFetchResult]) -> str:
        precedence = (
            'rate_limited', 'timeout_failed', 'incomplete', 'partial_saved',
            'completed_with_warning', 'completed',
        )
        statuses = {item.final_status for item in results}
        return next(status for status in precedence if status in statuses)

    @staticmethod
    def _audit_value(value: Any) -> str:
        return '' if value is None else str(value).replace('\r', ' ').replace('\n', ' ')

    @staticmethod
    def _merge_responses(
        api_responses: Sequence[dict[str, Any]],
    ) -> tuple[list[Any], int | None]:
        all_datas: list[Any] = []
        totals: list[int] = []
        for response_index, response in enumerate(api_responses):
            if not isinstance(response, dict):
                raise ValueError(f'api_responses[{response_index}] must be a dict')
            datas = response.get('datas', [])
            if not isinstance(datas, list):
                raise ValueError(f'api_responses[{response_index}]["datas"] must be a list')
            all_datas.extend(datas)

            total = response.get('total')
            if isinstance(total, int) and not isinstance(total, bool):
                totals.append(total)
            elif isinstance(total, str) and total.strip().isdigit():
                totals.append(int(total.strip()))
        return all_datas, max(totals) if totals else None

    @staticmethod
    def _normalize_valid_items(datas: Sequence[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item_index, item in enumerate(datas):
            if not isinstance(item, dict):
                logger.warning('Skipping invoice item index=%d because it is not an object', item_index)
                continue

            missing_fields = [
                field
                for field in REQUIRED_ITEM_FIELDS
                if field not in item or InvoiceOverviewStorageService._is_missing(item[field])
            ]
            if missing_fields:
                logger.warning(
                    'Skipping invoice item index=%d because required fields are missing: %s',
                    item_index,
                    ', '.join(missing_fields),
                )
                continue

            nlap = item.get('tdlap')
            if InvoiceOverviewStorageService._is_missing(nlap):
                nlap = item.get('ntao')
            normalized.append({
                'nbmst': item['nbmst'],
                'khhdon': item['khhdon'],
                'shdon': item['shdon'],
                'khmshdon': item['khmshdon'],
                'nlap': None if InvoiceOverviewStorageService._is_missing(nlap) else str(nlap),
                'nlap_date': InvoiceOverviewStorageService._normalize_invoice_date(nlap),
            })
        return normalized

    @staticmethod
    def _normalize_invoice_date(value: Any) -> str | None:
        if InvoiceOverviewStorageService._is_missing(value):
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).date().isoformat()
        except ValueError:
            pass
        for date_format in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(text[:10], date_format).date().isoformat()
            except ValueError:
                continue
        logger.warning('Could not normalize invoice date value=%r', text[:40])
        return None

    @staticmethod
    def _is_missing(value: Any) -> bool:
        return value is None or (isinstance(value, str) and not value.strip())

    @staticmethod
    def _validate_company_tax_code(company_tax_code: str) -> str:
        value = company_tax_code.strip()
        if not value:
            raise ValueError('company_tax_code must not be empty')
        if value in {'.', '..'} or '/' in value or '\\' in value:
            raise ValueError('company_tax_code must be a safe directory name')
        return value

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

    @staticmethod
    def _write_json_atomically(path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=path.parent,
                prefix=f'.{path.name}.',
                suffix='.tmp',
                delete=False,
            ) as temporary_file:
                json.dump(document, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write('\n')
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _write_text_atomically(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=path.parent,
                prefix=f'.{path.name}.',
                suffix='.tmp',
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            temporary_path.replace(path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
