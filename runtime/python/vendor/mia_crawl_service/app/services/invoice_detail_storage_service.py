from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY, VALID_DIRECTIONS
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.utils.date_utils import normalize_business_date
from app.config.runtime import RuntimeCapabilities
from app.parsers.invoice_detail_excel_row_builder import InvoiceDetailExcelRowBuilder

logger = logging.getLogger(__name__)

class InvoiceOverviewItemLike(Protocol):
    nbmst: Any
    khhdon: Any
    shdon: Any
    khmshdon: Any


class InvoiceDetailStorageService:
    """Write raw detail JSON and synchronize detail/overview database rows."""

    def __init__(
        self,
        data_root: Path | str,
        repository: InvoiceDetailRepository,
        progress_callback=None,
        capabilities: RuntimeCapabilities | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.repository = repository
        self.progress_callback = progress_callback
        self.capabilities = capabilities or RuntimeCapabilities.from_environment()

    def save_invoice_detail(
        self,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
        overview_item: Mapping[str, Any] | InvoiceOverviewItemLike,
        detail: dict[str, Any],
        from_date: str,
        to_date: str,
        http_status: int | None = 200,
    ) -> str:
        self._validate_inputs(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            invoice_category=invoice_category,
        )
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        keys = {
            name: str(self._overview_value(overview_item, name))
            for name in ('nbmst', 'khhdon', 'shdon', 'khmshdon')
        }
        fetched_at = datetime.now(timezone.utc).isoformat()
        raw_detail_path = self.raw_detail_path(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            **keys,
        )
        source_date = self._overview_value(overview_item, 'nlap', None)
        if source_date in (None, ''):
            source_date = self._overview_value(overview_item, 'tdlap', None)
        if source_date in (None, ''):
            source_date = self._overview_value(overview_item, 'ntao', None)
        nlap_date = normalize_business_date(source_date) or normalize_business_date(
            self._overview_value(overview_item, 'nlap_date', None)
        )
        document = {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'invoice_category': invoice_category,
            'invoice_key': keys,
            'nlap': source_date,
            'nlap_date': nlap_date,
            'fetched_at': fetched_at,
            'detail': detail,
        }
        self._progress('detail_payload_normalized')
        resolved_path: Path | str = ''
        if self.capabilities.retain_raw_artifacts:
            self._write_json_atomically(raw_detail_path, document)
            self._progress('raw_detail_written')
            resolved_path = raw_detail_path.resolve()
        if not self.capabilities.retain_raw_artifacts:
            normalized_lines = InvoiceDetailExcelRowBuilder().build_rows(
                document, {
                    **keys, 'nlap': source_date,
                    'material_codes_json': None,
                },
            )
            self.repository.replace_normalized_detail_success(
                company_tax_code=company_tax_code, direction=direction,
                query_type=query_type, invoice_category=invoice_category,
                **keys, nlap=self._optional_text(source_date), nlap_date=nlap_date,
                raw_detail_path='', http_status=http_status,
                fetched_at=fetched_at, lines=normalized_lines,
            )
            self._progress('detail_database_upserted')
            self._progress('overview_detail_link_updated')
            verified = self.repository.get_detail_by_invoice_key(
                company_tax_code, direction, query_type,
                keys['nbmst'], keys['khhdon'], keys['shdon'], keys['khmshdon'],
            )
            if not verified or not verified.get('normalized_ready'):
                raise RuntimeError('Persisted normalized invoice detail verification failed')
            self._progress('persisted_result_verified')
            logger.info('Saved normalized invoice detail invoice=%s', keys['shdon'])
            return ''

        self.repository.upsert_detail_success(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            invoice_category=invoice_category,
            **keys,
            nlap=self._optional_text(source_date),
            nlap_date=nlap_date,
            raw_detail_path=resolved_path,
            http_status=http_status,
            fetched_at=fetched_at,
        )
        self._progress('detail_database_upserted')
        updated_rows = self.repository.mark_overview_detail_fetched(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            **keys,
            raw_detail_path=resolved_path,
            updated_at=fetched_at,
        )
        if updated_rows != 1:
            raise RuntimeError(
                f'Expected one overview row while saving detail, updated={updated_rows}'
            )
        self._progress('overview_detail_link_updated')
        verified = self.repository.get_detail_by_invoice_key(
            company_tax_code, direction, query_type,
            keys['nbmst'], keys['khhdon'], keys['shdon'], keys['khmshdon'],
        )
        if not Path(resolved_path).is_file() or not verified:
            raise RuntimeError('Persisted invoice detail verification failed')
        self._progress('persisted_result_verified')
        logger.info('Saved raw invoice detail path=%s', raw_detail_path)
        return str(resolved_path)

    def _progress(self, event: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(event)

    def raw_detail_path(
        self,
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
    ) -> Path:
        keys = {
            'nbmst': str(nbmst), 'khhdon': str(khhdon),
            'shdon': str(shdon), 'khmshdon': str(khmshdon),
        }
        filename = '_'.join(
            self._sanitize_filename_component(keys[name])
            for name in ('khmshdon', 'khhdon', 'shdon', 'nbmst')
        ) + '.json'
        return (
            self.data_root / company_tax_code / 'raw' / 'invoice_details'
            / direction / query_type / f'{from_date}_{to_date}' / filename
        )

    @staticmethod
    def _validate_inputs(
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        invoice_category: str,
    ) -> None:
        if not company_tax_code.strip() or company_tax_code in {'.', '..'}:
            raise ValueError('company_tax_code must not be empty')
        if '/' in company_tax_code or '\\' in company_tax_code:
            raise ValueError('company_tax_code must be a safe directory name')
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        expected_category = QUERY_TYPE_TO_CATEGORY.get(query_type)
        if expected_category is None:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        if invoice_category != expected_category:
            raise ValueError(
                f'invoice_category must be {expected_category!r} for {query_type!r}'
            )

    @staticmethod
    def _sanitize_filename_component(value: str) -> str:
        sanitized = re.sub(r'[^A-Za-z0-9._-]+', '_', value).strip('._')
        return (sanitized or 'unknown')[:100]

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _overview_value(
        overview_item: Mapping[str, Any] | InvoiceOverviewItemLike,
        name: str,
        default: Any = ...,
    ) -> Any:
        """Read an overview field from either a mapping or a dataclass-like object."""
        if isinstance(overview_item, Mapping):
            if default is ...:
                return overview_item[name]
            return overview_item.get(name, default)
        if hasattr(overview_item, name):
            return getattr(overview_item, name)
        if default is not ...:
            return default
        raise KeyError(name)

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
