from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY, VALID_DIRECTIONS
from app.crawlers.invoice_detail_crawler import (
    InvoiceDetailCrawler,
    build_detail_headers,
)
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.services.invoice_detail_storage_service import InvoiceDetailStorageService

logger = logging.getLogger(__name__)

class InvoiceDetailDownloadService:
    """Orchestrate cached overview lookup, detail fetching and persistence."""

    def __init__(
        self,
        detail_crawler: InvoiceDetailCrawler,
        overview_repository: InvoiceOverviewRepository,
        detail_storage_service: InvoiceDetailStorageService,
        detail_repository: InvoiceDetailRepository,
        overview_fetcher: Callable[..., Any] | None = None,
        delay_between_items_seconds: float = 0.0,
    ) -> None:
        if delay_between_items_seconds < 0:
            raise ValueError('delay_between_items_seconds must not be negative')
        self.detail_crawler = detail_crawler
        self.overview_repository = overview_repository
        self.detail_storage_service = detail_storage_service
        self.detail_repository = detail_repository
        self.overview_fetcher = overview_fetcher
        self.delay_between_items_seconds = delay_between_items_seconds

    def download_invoice_details(
        self,
        headers: dict[str, str],
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        fetch_new_overview: bool = False,
        only_pending: bool = True,
        max_details: int | None = None,
    ) -> dict[str, Any]:
        invoice_category = self._validate_request(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            max_details=max_details,
        )
        logger.info(
            'Starting invoice detail batch company=%s direction=%s query_type=%s '
            'range=%s..%s fetch_new_overview=%s only_pending=%s limit=%s',
            company_tax_code, direction, query_type, from_date, to_date,
            fetch_new_overview, only_pending, max_details,
        )

        local_count = self.overview_repository.count_overview_items(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
        )
        if fetch_new_overview:
            self._fetch_overview(
                headers=headers,
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                invoice_category=invoice_category,
                from_date=from_date,
                to_date=to_date,
            )
        elif local_count == 0:
            logger.warning(
                'No local overview data for company=%s direction=%s query_type=%s '
                'range=%s..%s; fetching overview first',
                company_tax_code, direction, query_type, from_date, to_date,
            )
            self._fetch_overview(
                headers=headers,
                company_tax_code=company_tax_code,
                direction=direction,
                query_type=query_type,
                invoice_category=invoice_category,
                from_date=from_date,
                to_date=to_date,
            )
        else:
            logger.info('Using %d local overview items; list API will not be called', local_count)

        items = self.overview_repository.get_items_for_detail_download(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=from_date,
            to_date=to_date,
            only_pending=only_pending,
            limit=max_details,
        )
        logger.info('Selected %d overview items for invoice detail download', len(items))
        downloaded_count = 0
        failed_count = 0
        detail_headers = build_detail_headers(headers, direction)

        for current, item in enumerate(items, start=1):
            try:
                detail = self.detail_crawler.get_invoice_detail(
                    headers=detail_headers,
                    query_type=query_type,
                    nbmst=item['nbmst'],
                    khhdon=item['khhdon'],
                    shdon=item['shdon'],
                    khmshdon=item['khmshdon'],
                )
                self.detail_storage_service.save_invoice_detail(
                    company_tax_code=company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    invoice_category=invoice_category,
                    overview_item=item,
                    detail=detail,
                    from_date=from_date,
                    to_date=to_date,
                )
                downloaded_count += 1
                logger.info(
                    '[%d/%d] detail downloaded nbmst=%s khhdon=%s shdon=%s khmshdon=%s',
                    current, len(items), item['nbmst'], item['khhdon'],
                    item['shdon'], item['khmshdon'],
                )
            except Exception as error:
                failed_count += 1
                status = self._find_http_status(error)
                attempted_at = datetime.now(timezone.utc).isoformat()
                try:
                    self.detail_repository.upsert_detail_error(
                        company_tax_code=company_tax_code,
                        direction=direction,
                        query_type=query_type,
                        invoice_category=invoice_category,
                        nbmst=item['nbmst'],
                        khhdon=item['khhdon'],
                        shdon=item['shdon'],
                        khmshdon=item['khmshdon'],
                        nlap=item.get('nlap'),
                        nlap_date=item.get('nlap_date'),
                        http_status=status,
                        error_message=f'{type(error).__name__}: {error}',
                        attempted_at=attempted_at,
                    )
                except Exception:
                    logger.exception(
                        '[%d/%d] could not persist detail error nbmst=%s '
                        'khhdon=%s shdon=%s khmshdon=%s',
                        current, len(items), item['nbmst'], item['khhdon'],
                        item['shdon'], item['khmshdon'],
                    )
                logger.error(
                    '[%d/%d] detail failed nbmst=%s khhdon=%s shdon=%s '
                    'khmshdon=%s http=%s error=%s',
                    current, len(items), item['nbmst'], item['khhdon'],
                    item['shdon'], item['khmshdon'], status,
                    type(error).__name__,
                )
                # Persistent authentication/rate-limit errors are batch-level
                # session conditions. Continuing would only repeat failures.
                if status in {401, 429}:
                    raise

            if current < len(items) and self.delay_between_items_seconds > 0:
                logger.debug(
                    'Waiting %.2fs before the next invoice detail',
                    self.delay_between_items_seconds,
                )
                time.sleep(self.delay_between_items_seconds)

        details_dir = (
            self.detail_storage_service.data_root
            / company_tax_code
            / 'raw'
            / 'invoice_details'
            / direction
            / query_type
            / f'{from_date}_{to_date}'
        ).resolve()
        summary = {
            'company_tax_code': company_tax_code,
            'direction': direction,
            'query_type': query_type,
            'invoice_category': invoice_category,
            'from_date': from_date,
            'to_date': to_date,
            'fetch_new_overview': fetch_new_overview,
            'total_items': len(items),
            'downloaded_count': downloaded_count,
            'failed_count': failed_count,
            'skipped_count': 0,
            'details_dir': str(details_dir),
            'db_path': str(self.overview_repository.database_path.resolve()),
        }
        logger.info('Completed invoice detail batch summary=%s', summary)
        return summary

    def _fetch_overview(self, **kwargs: Any) -> None:
        if self.overview_fetcher is None:
            raise RuntimeError(
                'Overview data is missing and overview_fetcher is not configured'
            )
        logger.info('Fetching overview before invoice detail download')
        self.overview_fetcher(**kwargs)

    @staticmethod
    def _validate_request(
        *,
        company_tax_code: str,
        direction: str,
        query_type: str,
        from_date: str,
        to_date: str,
        max_details: int | None,
    ) -> str:
        if not company_tax_code.strip():
            raise ValueError('company_tax_code must not be empty')
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f'Unsupported direction: {direction!r}')
        invoice_category = QUERY_TYPE_TO_CATEGORY.get(query_type)
        if invoice_category is None:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        if max_details is not None and max_details <= 0:
            raise ValueError('max_details must be greater than zero')
        return invoice_category

    @staticmethod
    def _find_http_status(error: BaseException) -> int | None:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            response = getattr(current, 'response', None)
            status_code = getattr(response, 'status_code', None)
            if isinstance(status_code, int) and not isinstance(status_code, bool):
                return status_code
            current = current.__cause__ or current.__context__
        return None
