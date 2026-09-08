from __future__ import annotations

import logging
import io
import shutil
import tempfile
import time
from copy import copy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:
    from openpyxl.cell.cell import Cell

from app.config.crawl_config import category_to_query_type
from app.crawlers.invoice_crawler import (
    IncompleteCursorError,
    InvoiceCrawler,
    InvoiceFetchResult,
    InvoiceRateLimitError,
    RepeatedCursorError,
)
from app.models.overview import (
    OverviewCheckpointError,
    OverviewDownloadRequest,
    OverviewPageCommit,
)
from app.services.invoice_overview_storage_service import InvoiceOverviewStorageService
from app.utils.date_utils import split_by_calendar_month
from app.utils.date_utils import BUSINESS_TIMEZONE

logger = logging.getLogger(__name__)


def _load_workbook(*args, **kwargs):
    from openpyxl import load_workbook
    return load_workbook(*args, **kwargs)


def _new_workbook():
    from openpyxl import Workbook
    return Workbook()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / 'resources' / 'templates'
ELECTRONIC_STATUSES = (5, 6, 8)
HEADER_MARKER = 'STT'
INVOICE_STATUS_LABELS = {
    1: 'Hóa đơn mới',
    2: 'Hóa đơn thay thế',
    3: 'Hóa đơn điều chỉnh',
    4: 'Hóa đơn đã bị thay thế',
    5: 'Hóa đơn đã bị điều chỉnh',
    6: 'Hóa đơn đã bị hủy',
}

OUTPUT_NAMES = {
    ('purchase', 'electronic'): 'DANH SÁCH HÓA ĐƠN MUA VÀO.xlsx',
    ('purchase', 'cash_register'): 'DANH SÁCH HÓA ĐƠN MÁY TÍNH TIỀN MUA VÀO.xlsx',
    ('sold', 'electronic'): 'DANH SÁCH HÓA ĐƠN BÁN RA.xlsx',
    ('sold', 'cash_register'): 'DANH SÁCH HÓA ĐƠN MÁY TÍNH TIỀN BÁN RA.xlsx',
}


@dataclass(frozen=True)
class DownloadReport:
    direction: str
    category: str
    output_path: Path
    source_files: int
    input_rows: int
    output_rows: int
    duplicate_rows: int
    json_total: int | None = None
    count_matches: bool | None = None


def _find_header_row(ws) -> int:
    for row in range(1, min(ws.max_row, 30) + 1):
        if ws.cell(row, 1).value == HEADER_MARKER:
            return row
    raise RuntimeError(f'Cannot find {HEADER_MARKER!r} header row in worksheet {ws.title!r}')


def _clone_cell(source: Cell, target: Cell) -> None:
    target.value = source.value
    if source.has_style:
        if source.parent.parent is target.parent.parent:
            # Style IDs are already registered in the same workbook.
            target._style = copy(source._style)
        else:
            # Cross-workbook copies must register each style component in the
            # destination workbook; copying _style IDs directly corrupts it.
            target.font = copy(source.font)
            target.fill = copy(source.fill)
            target.border = copy(source.border)
            target.alignment = copy(source.alignment)
            target.protection = copy(source.protection)
            target.number_format = source.number_format
    if source.hyperlink:
        target._hyperlink = copy(source.hyperlink)
    if source.comment:
        target.comment = copy(source.comment)


def _date_sort_value(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        for fmt in ('%d/%m/%Y', '%d/%m/%Y %H:%M:%S'):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                pass
    return datetime.min


class OverviewDownloader:
    def __init__(
        self,
        *,
        crawler: InvoiceCrawler,
        headers_provider,
        template_dir: Path = DEFAULT_TEMPLATE_DIR,
        keep_temp: bool = False,
        storage_service: InvoiceOverviewStorageService | None = None,
        company_tax_code: str | None = None,
        page_committed=None,
        interruption_check=None,
        initial_payloads=None,
    ) -> None:
        if (storage_service is None) != (company_tax_code is None):
            raise ValueError(
                'storage_service and company_tax_code must be configured together'
            )
        self.crawler = crawler
        self.headers_provider = headers_provider
        self.template_dir = Path(template_dir)
        self.keep_temp = keep_temp
        self.storage_service = storage_service
        self.company_tax_code = company_tax_code
        self.page_committed = page_committed
        self.interruption_check = interruption_check
        self.initial_payloads = initial_payloads or {}
        self.storage_summaries: list[dict[str, object]] = []
        self.overview_warnings: list[dict[str, object]] = []
        self.last_temp_dir: Path | None = None

    def download_request(
        self,
        request: OverviewDownloadRequest,
    ) -> list[DownloadReport]:
        """Run the overview application flow from one shared input model."""
        return self.download(
            begin_date=request.begin_date,
            end_date=request.end_date,
            output_dir=request.output_dir,
            directions=request.directions,
            categories=request.categories,
            overwrite=request.overwrite,
            combined_workbook_name=request.combined_workbook_name,
        )

    def download_normalized_request(
        self, request: OverviewDownloadRequest
    ) -> dict[str, object]:
        """Persist monthly pages/checkpoints and return durable source warnings."""
        if self.storage_service is None or self.company_tax_code is None:
            raise ValueError('normalized download requires durable storage')
        self.overview_warnings = []
        for direction in request.directions:
            for category in request.categories:
                query_type = category_to_query_type(category)
                for begin, end in split_by_calendar_month(
                    request.begin_date, request.end_date
                ):
                    results: list[tuple[int | None, InvoiceFetchResult]] = []
                    for status in self._job_statuses(direction, category):
                        result = self._fetch_records_resilient(
                            direction=direction, category=category,
                            begin_date=begin, end_date=end, status=status,
                        )
                        results.append((status, result))
                        if (
                            result.final_status == 'completed_with_warning'
                            and result.first_page_total is not None
                            and result.fetched_count != result.first_page_total
                        ):
                            warning = {
                                'stage': 'overview',
                                'code': 'source_total_mismatch',
                                'direction': direction,
                                'query_type': query_type,
                                'status_filter': 'all' if status is None else str(status),
                                'from_date': begin.isoformat(),
                                'to_date': end.isoformat(),
                                'expected_total': result.first_page_total,
                                'fetched_count': result.fetched_count,
                                'missing_count': result.missing_count,
                                'source_state': 'end',
                            }
                            self.overview_warnings.append(warning)
                            logger.warning(
                                'Overview source warning direction=%s query_type=%s '
                                'range=%s..%s status=%s fetched=%s total=%s missing=%s',
                                direction, query_type, begin, end, status,
                                result.fetched_count, result.first_page_total,
                                result.missing_count,
                            )
                        if (
                            result.final_status in {'completed', 'completed_with_warning'}
                            and not self.storage_service.refresh_run_id
                        ):
                            self.storage_service.finalize_invoice_overview_checkpoint(
                                company_tax_code=self.company_tax_code,
                                direction=direction, query_type=query_type,
                                from_date=begin.isoformat(), to_date=end.isoformat(),
                                status=status,
                            )
                    options = getattr(self.crawler, 'paging_options', None)
                    if (
                        getattr(options, 'write_page_audit_report', False)
                        and any(result.needs_audit for _, result in results)
                    ):
                        self.storage_service.write_overview_audit_report(
                            company_tax_code=self.company_tax_code,
                            direction=direction, query_type=query_type,
                            from_date=begin.isoformat(), to_date=end.isoformat(),
                            profile=getattr(options, 'profile', 'unknown'),
                            results=[result for _, result in results],
                            report_dir_name=getattr(
                                options, 'incomplete_report_dir_name', 'overview_audit'
                            ),
                        )
                    incomplete = next((
                        result for _, result in results
                        if result.final_status not in {'completed', 'completed_with_warning'}
                    ), None)
                    if incomplete is not None:
                        raise IncompleteCursorError(
                            'Overview range is incomplete; committed pages were retained',
                            incomplete,
                        )
                    if self.storage_service.refresh_run_id:
                        self.storage_service.activate_invoice_overview_refresh(
                            company_tax_code=self.company_tax_code,
                            direction=direction, query_type=query_type,
                            from_date=begin.isoformat(), to_date=end.isoformat(),
                            statuses=self._job_statuses(direction, category),
                        )
                    largest = max(
                        (result for _, result in results),
                        key=lambda item: {'light': 0, 'heavy': 1, 'extreme': 2}[
                            item.range_class
                        ],
                    )
                    self._sleep_after_large_range(largest, direction, category)
        return {
            'warning_count': len(self.overview_warnings),
            'warnings': [dict(item) for item in self.overview_warnings],
        }

    def _template_path(self, category: str, direction: str) -> Path:
        specific = self.template_dir / f'{category}_{direction}.xlsx'
        return specific if specific.is_file() else self.template_dir / f'{category}.xlsx'

    def download(
        self,
        *,
        begin_date: date,
        end_date: date,
        output_dir: Path | str,
        directions: Sequence[str] = ('purchase', 'sold'),
        categories: Sequence[str] = ('electronic', 'cash_register'),
        overwrite: bool = False,
        combined_workbook_name: str | None = None,
    ) -> list[DownloadReport]:
        ranges = split_by_calendar_month(begin_date, end_date)
        self.storage_summaries = []
        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        jobs = [(d, c) for d in directions for c in categories]
        for job in jobs:
            if job not in OUTPUT_NAMES:
                raise ValueError(f'Unsupported download job: {job}')
            if not combined_workbook_name:
                target = output_dir / OUTPUT_NAMES[job]
                if target.exists() and not overwrite:
                    raise FileExistsError(f'Output already exists: {target}')
        combined_target = output_dir / combined_workbook_name if combined_workbook_name else None
        if combined_target and combined_target.exists() and not overwrite:
            raise FileExistsError(f'Output already exists: {combined_target}')

        temp_dir = Path(tempfile.mkdtemp(prefix='.tax-overview-', dir=output_dir))
        self.last_temp_dir = temp_dir
        reports: list[DownloadReport] = []
        staged_outputs: list[tuple[Path, Path]] = []
        staged_jobs: list[tuple[str, str, Path]] = []
        try:
            for direction, category in jobs:
                source_paths, json_total, has_partial = self._download_job(
                    temp_dir=temp_dir,
                    direction=direction,
                    category=category,
                    ranges=ranges,
                )
                target = output_dir / OUTPUT_NAMES[(direction, category)]
                staged_target = temp_dir / f'merged-{direction}-{category}.xlsx'
                report = self.merge_exports(
                    source_paths=source_paths,
                    target_path=staged_target,
                    direction=direction,
                    category=category,
                    begin_date=begin_date,
                    end_date=end_date,
                )
                count_matches = (
                    report.output_rows == json_total if json_total is not None else False
                )
                if not count_matches and not has_partial:
                    logger.warning(
                        'Count changed between JSON and export; rechecking JSON once for %s/%s',
                        direction, category,
                    )
                    json_total = self._audit_job_total(
                        direction=direction,
                        category=category,
                        ranges=ranges,
                    )
                    count_matches = report.output_rows == json_total
                reports.append(replace(
                    report,
                    output_path=target,
                    json_total=json_total,
                    count_matches=count_matches,
                ))
                if count_matches:
                    logger.info(
                        'JSON/Excel count matched for %s/%s: %s rows',
                        direction, category, json_total,
                    )
                elif has_partial:
                    logger.warning(
                        'Overview job has partial ranges for %s/%s: expected JSON=%s, '
                        'Excel rows=%s; automatic recount skipped',
                        direction, category, json_total, report.output_rows,
                    )
                else:
                    logger.warning(
                        'JSON/Excel count mismatch for %s/%s: JSON total=%s, Excel rows=%s',
                        direction, category, json_total, report.output_rows,
                    )
                staged_outputs.append((staged_target, target))
                staged_jobs.append((direction, category, staged_target))
                logger.info('Prepared %s with %s rows', target, report.output_rows)
            # Publish only after every requested job is complete. Existing
            # output files remain untouched when any API/schema step fails.
            if combined_target:
                combined_staged = temp_dir / 'merged-all-categories.xlsx'
                self.combine_workbooks(staged_jobs, combined_staged)
                combined_staged.replace(combined_target)
                reports = [DownloadReport(
                    direction=reports[0].direction if len(set(directions)) == 1 else 'all',
                    category='all',
                    output_path=combined_target,
                    source_files=sum(report.source_files for report in reports),
                    input_rows=sum(report.input_rows for report in reports),
                    output_rows=sum(report.output_rows for report in reports),
                    duplicate_rows=sum(report.duplicate_rows for report in reports),
                    json_total=(
                        sum(report.json_total for report in reports if report.json_total is not None)
                        if all(report.json_total is not None for report in reports)
                        else None
                    ),
                    count_matches=all(report.count_matches for report in reports),
                )]
            else:
                for staged_target, target in staged_outputs:
                    staged_target.replace(target)
            for report in reports:
                logger.info('Created %s with %s rows', report.output_path, report.output_rows)
            return reports
        finally:
            if not self.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=False)

    @staticmethod
    def combine_workbooks(
        staged_jobs: Sequence[tuple[str, str, Path]],
        target_path: Path,
    ) -> None:
        """Combine category workbooks as styled sheets in one XLSX file."""
        started = time.perf_counter()
        sheet_names = {
            'electronic': 'Hóa đơn điện tử',
            'cash_register': 'Máy tính tiền',
        }
        direction_names = {
            'purchase': 'Mua vào',
            'sold': 'Bán ra',
        }
        multiple_directions = len({direction for direction, _, _ in staged_jobs}) > 1
        target_wb = _new_workbook()
        target_wb.remove(target_wb.active)
        for direction, category, source_path in staged_jobs:
            source_wb = _load_workbook(source_path)
            source_ws = source_wb.active
            title = sheet_names.get(category, category)
            if multiple_directions:
                title = f'{title} - {direction_names.get(direction, direction)}'[:31]
            if title in target_wb.sheetnames:
                title = f'{title} {direction}'[:31]
            target_ws = target_wb.create_sheet(title=title)
            source_header_row = _find_header_row(source_ws)
            source_prototype_row = source_header_row + 1

            for row in source_ws.iter_rows():
                for source_cell in row:
                    target_cell = target_ws.cell(source_cell.row, source_cell.column)
                    if category == 'cash_register' and source_cell.row > source_prototype_row:
                        # The prototype row has already registered the correct
                        # per-column style in this destination workbook.
                        target_cell.value = source_cell.value
                        prototype = target_ws.cell(source_prototype_row, source_cell.column)
                        target_cell._style = copy(prototype._style)
                    else:
                        _clone_cell(source_cell, target_cell)
            for key, dimension in source_ws.column_dimensions.items():
                target_ws.column_dimensions[key].width = dimension.width
                target_ws.column_dimensions[key].hidden = dimension.hidden
            for index, dimension in source_ws.row_dimensions.items():
                target_ws.row_dimensions[index].height = dimension.height
                target_ws.row_dimensions[index].hidden = dimension.hidden
            for merged_range in source_ws.merged_cells.ranges:
                target_ws.merge_cells(str(merged_range))
            target_ws.freeze_panes = source_ws.freeze_panes
            target_ws.auto_filter.ref = source_ws.auto_filter.ref
            target_ws.sheet_view.showGridLines = source_ws.sheet_view.showGridLines
            target_ws.page_margins = copy(source_ws.page_margins)
            target_ws.page_setup = copy(source_ws.page_setup)
            target_ws.sheet_properties = copy(source_ws.sheet_properties)
            source_wb.close()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_wb.save(target_path)
        target_wb.close()
        logger.info(
            'Combined %d worksheets into %s in %.2fs',
            len(staged_jobs), target_path, time.perf_counter() - started,
        )

    def _download_job(
        self,
        *,
        temp_dir: Path,
        direction: str,
        category: str,
        ranges: Iterable[tuple[date, date]],
    ) -> tuple[list[Path], int | None, bool]:
        paths: list[Path] = []
        known_json_total = 0
        has_unknown_total = False
        has_partial = False
        # Electronic purchase/sold tabs are queried as 5/6/8 and merged.
        # For cash-register invoices the portal's "all" query is complete,
        # while its ttxly=6 endpoint consistently returns 504 even for empty
        # results (verified against the supplied account on 2026-07-14).
        statuses = self._job_statuses(direction, category)
        for range_index, (chunk_begin, chunk_end) in enumerate(ranges, start=1):
            batch_records: list[dict] = []
            batch_totals: list[int | None] = []
            batch_results: list[tuple[int | None, InvoiceFetchResult]] = []
            for status in statuses:
                result = self._fetch_records_resilient(
                    direction=direction,
                    category=category,
                    begin_date=chunk_begin,
                    end_date=chunk_end,
                    status=status,
                )
                records = result.records
                interval_total = result.first_page_total
                if interval_total is None:
                    has_unknown_total = True
                else:
                    known_json_total += interval_total
                batch_totals.append(interval_total)
                batch_records.extend(records)
                batch_results.append((status, result))
                has_partial = has_partial or result.final_status not in {
                    'completed', 'completed_with_warning'
                }

            # Persist the complete monthly list batch before any optional Excel
            # export. A flaky export must not discard list data already crawled.
            if self.storage_service is not None and self.company_tax_code is not None:
                query_type = category_to_query_type(category)
                batch_total = (
                    sum(value for value in batch_totals if value is not None)
                    if all(value is not None for value in batch_totals)
                    else None
                )
                summary = self.storage_service.save_invoice_overview_batch(
                    company_tax_code=self.company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    from_date=chunk_begin.isoformat(),
                    to_date=chunk_end.isoformat(),
                    api_responses=[{
                        'datas': batch_records,
                        'total': batch_total,
                        'state': None,
                    }],
                    final_status=self._aggregate_range_status(
                        [result for _, result in batch_results]
                    ),
                    checkpoint_state=next(
                        (
                            result.checkpoint_state
                            for _, result in reversed(batch_results)
                            if result.checkpoint_state
                        ),
                        None,
                    ),
                )
                self.storage_summaries.append(summary)

                options = getattr(self.crawler, 'paging_options', None)
                if (
                    getattr(options, 'write_page_audit_report', False)
                    and any(result.needs_audit for _, result in batch_results)
                ):
                    report_path = self.storage_service.write_overview_audit_report(
                        company_tax_code=self.company_tax_code,
                        direction=direction,
                        query_type=query_type,
                        from_date=chunk_begin.isoformat(),
                        to_date=chunk_end.isoformat(),
                        profile=getattr(options, 'profile', 'unknown'),
                        results=[result for _, result in batch_results],
                        report_dir_name=getattr(
                            options, 'incomplete_report_dir_name', 'overview_audit'
                        ),
                    )
                    logger.warning(
                        'Overview audit written direction=%s query_type=%s range=%s..%s '
                        'status=%s report=%s',
                        direction, query_type, chunk_begin, chunk_end,
                        summary['final_status'], report_path,
                    )
                    if summary['final_status'] == 'incomplete':
                        logger.error(
                            'Overview incomplete direction=%s query_type=%s range=%s..%s '
                            'fetched=%s total=%s missing=%s report=%s',
                            direction, query_type, chunk_begin, chunk_end,
                            len(batch_records), batch_total,
                            (
                                max(batch_total - len(batch_records), 0)
                                if batch_total is not None else 'unknown'
                            ),
                            report_path,
                        )
                if (
                    any(result.final_status == 'incomplete' for _, result in batch_results)
                    and not getattr(options, 'continue_on_incomplete', True)
                ):
                    incomplete_result = next(
                        result
                        for _, result in batch_results
                        if result.final_status == 'incomplete'
                    )
                    raise IncompleteCursorError(
                        'Overview range is incomplete; partial data and audit were saved',
                        incomplete_result,
                    )

            for status, result in batch_results:
                records = result.records
                status_label = 'all' if status is None else str(status)
                name = f'{direction}-{category}-{range_index:04d}-status-{status_label}.xlsx'
                path = temp_dir / name
                if category == 'cash_register':
                    # Monthly sco export/list endpoints frequently time out on
                    # historical data for either direction. We already have
                    # every paged JSON record, so render it into the styled
                    # template locally instead of making a redundant export.
                    content = self._cash_records_to_xlsx(records, direction, chunk_begin, chunk_end)
                else:
                    # The paginated JSON above is already the authoritative
                    # dataset. Calling the portal's separate Excel endpoint
                    # here duplicates the request and can hang for minutes
                    # after all rows have been fetched. Render locally for
                    # both complete and partial electronic results.
                    if result.final_status == 'completed':
                        logger.info(
                            'Rendering electronic workbook locally direction=%s '
                            'range=%s..%s fetched=%s/%s',
                            direction, chunk_begin, chunk_end,
                            len(records), result.first_page_total,
                        )
                    else:
                        logger.warning(
                            'Rendering partial electronic workbook locally status=%s '
                            'direction=%s range=%s..%s fetched=%s/%s',
                            result.final_status, direction, chunk_begin, chunk_end,
                            len(records), result.first_page_total,
                        )
                    content = self._electronic_records_to_xlsx(
                        records, direction, chunk_begin, chunk_end
                    )
                path.write_bytes(content)
                # Parse now so corrupt/error workbooks fail before merge.
                workbook = _load_workbook(path, read_only=True, data_only=False)
                workbook.close()
                paths.append(path)
            if self.storage_service is not None and self.company_tax_code is not None:
                query_type = category_to_query_type(category)
                for status, result in batch_results:
                    if result.final_status in {'completed', 'completed_with_warning'}:
                        self.storage_service.finalize_invoice_overview_checkpoint(
                            company_tax_code=self.company_tax_code,
                            direction=direction,
                            query_type=query_type,
                            from_date=chunk_begin.isoformat(),
                            to_date=chunk_end.isoformat(),
                            status=status,
                        )
            # A month/query_type is one logical persisted range even when the
            # portal requires several status cursors. Apply one post-range
            # cooldown using the largest measured cursor workload.
            range_rank = {'light': 0, 'heavy': 1, 'extreme': 2}
            largest_result = max(
                (result for _, result in batch_results),
                key=lambda item: range_rank[item.range_class],
            )
            self._sleep_after_large_range(largest_result, direction, category)
        return paths, None if has_unknown_total else known_json_total, has_partial

    @staticmethod
    def _job_statuses(direction: str, category: str) -> tuple[int | None, ...]:
        return (
            ELECTRONIC_STATUSES
            if category == 'electronic'
            else (None,)
        )

    def _audit_job_total(
        self,
        *,
        direction: str,
        category: str,
        ranges: Iterable[tuple[date, date]],
    ) -> int:
        total = 0
        for chunk_begin, chunk_end in ranges:
            for status in self._job_statuses(direction, category):
                result = self._fetch_records_resilient(
                    direction=direction,
                    category=category,
                    begin_date=chunk_begin,
                    end_date=chunk_end,
                    status=status,
                )
                total += (
                    result.first_page_total
                    if result.first_page_total is not None
                    else len(result.records)
                )
        return total

    def _fetch_records_resilient(
        self,
        *,
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
        status: int | None,
    ) -> InvoiceFetchResult:
        query_type = category_to_query_type(category)
        resume_state = None
        page_committer = None
        if self.storage_service is not None and self.company_tax_code is not None:
            resume_state = self.storage_service.load_invoice_overview_resume_state(
                company_tax_code=self.company_tax_code,
                direction=direction,
                query_type=query_type,
                from_date=begin_date.isoformat(),
                to_date=end_date.isoformat(),
                status=status,
            )

            def commit_page(page: OverviewPageCommit) -> None:
                result = self.storage_service.save_invoice_overview_page(
                    company_tax_code=self.company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    from_date=begin_date.isoformat(),
                    to_date=end_date.isoformat(),
                    status=status,
                    page=page,
                )
                if self.page_committed is not None:
                    self.page_committed(page, result, {
                        'direction': direction,
                        'query_type': query_type,
                        'status_filter': 'all' if status is None else str(status),
                        'from_date': begin_date.isoformat(),
                        'to_date': end_date.isoformat(),
                    })

            page_committer = commit_page
        try:
            result = self.crawler.fetch_all_adaptive(
                headers=self.headers_provider(),
                direction=direction,
                category=category,
                begin_date=begin_date,
                end_date=end_date,
                status=status,
                resume_state=resume_state,
                initial_payload=self.initial_payloads.get(
                    'all' if status is None else str(status)
                ),
                page_committer=page_committer,
                interruption_check=self.interruption_check,
                retain_records=(
                    self.storage_service is None
                    or self.storage_service.capabilities.retain_overview_records
                ),
                retain_page_details=(
                    self.storage_service is None
                    or self.storage_service.capabilities.retain_overview_records
                ),
            )
            logger.info(
                'Overview range result %s/%s %s..%s status_filter=%s: '
                'final_status=%s total=%s fetched=%s pages=%s size=%s',
                direction, category, begin_date, end_date, status,
                result.final_status,
                result.first_page_total, result.fetched_count, result.pages,
                result.final_page_size,
            )
            if result.first_page_total != result.fetched_count:
                logger.error(
                    'Overview incomplete direction=%s query_type=%s range=%s..%s '
                    'fetched=%s total=%s missing=%s report=pending',
                    direction, category_to_query_type(category), begin_date, end_date,
                    result.fetched_count,
                    result.first_page_total, result.missing_count,
                )
            return result
        except IncompleteCursorError as error:
            # The caller persists the partial rows/report before honoring the
            # configured fail-fast behavior.
            if self.storage_service is None:
                raise
            return error.result
        except InvoiceRateLimitError:
            # 429 is unrelated to date-range size. Splitting would only send
            # more requests and worsen the rate limit.
            raise
        except (OverviewCheckpointError, RepeatedCursorError):
            # Persistence/cursor integrity errors are unrelated to range size.
            # Splitting would hide the broken checkpoint or repeat bad data.
            raise
        except RuntimeError:
            if begin_date >= end_date:
                raise
            midpoint = begin_date + timedelta(days=(end_date - begin_date).days // 2)
            logger.warning(
                'JSON query failed for %s/%s %s..%s; splitting at %s',
                direction, category, begin_date, end_date, midpoint,
            )
            left_result = self._fetch_records_resilient(
                direction=direction,
                category=category,
                begin_date=begin_date,
                end_date=midpoint,
                status=status,
            )
            right_result = self._fetch_records_resilient(
                direction=direction,
                category=category,
                begin_date=midpoint + timedelta(days=1),
                end_date=end_date,
                status=status,
            )
            combined = self._combine_split_results(left_result, right_result)
            if (
                self.storage_service is not None
                and self.company_tax_code is not None
                and self.storage_service.refresh_run_id
                and combined.final_status in {'completed', 'completed_with_warning'}
            ):
                self.storage_service.reconcile_split_invoice_overview_checkpoint(
                    company_tax_code=self.company_tax_code,
                    direction=direction,
                    query_type=query_type,
                    from_date=begin_date.isoformat(),
                    to_date=end_date.isoformat(),
                    status=status,
                    result=combined,
                )
            return combined

    @staticmethod
    def _aggregate_range_status(results: Sequence[InvoiceFetchResult]) -> str:
        precedence = (
            'rate_limited', 'timeout_failed', 'incomplete', 'partial_saved',
            'completed_with_warning', 'completed',
        )
        statuses = {result.final_status for result in results}
        return next(status for status in precedence if status in statuses)

    @classmethod
    def _combine_split_results(
        cls,
        left: InvoiceFetchResult,
        right: InvoiceFetchResult,
    ) -> InvoiceFetchResult:
        total = (
            left.first_page_total + right.first_page_total
            if left.first_page_total is not None and right.first_page_total is not None
            else None
        )
        page_offset = max(
            left.pages,
            max((detail.page for detail in left.page_details), default=0),
        )
        right_details = tuple(
            replace(
                detail,
                page=page_offset + detail.page,
                fetched_before=left.fetched_count + detail.fetched_before,
                fetched_after=left.fetched_count + detail.fetched_after,
            )
            for detail in right.page_details
        )
        right_suspicious = tuple(
            replace(item, page=page_offset + item.page)
            for item in right.suspicious_pages
        )
        range_rank = {'light': 0, 'heavy': 1, 'extreme': 2}
        range_class = max(
            (left.range_class, right.range_class), key=lambda item: range_rank[item]
        )
        return InvoiceFetchResult(
            records=[*left.records, *right.records],
            first_page_total=total,
            pages=left.pages + right.pages,
            final_page_size=right.final_page_size,
            final_status=cls._aggregate_range_status((left, right)),
            final_state=right.final_state,
            estimated_pages=left.estimated_pages + right.estimated_pages,
            range_class=range_class,
            page_details=(*left.page_details, *right_details),
            suspicious_pages=(*left.suspicious_pages, *right_suspicious),
            retry_count=left.retry_count + right.retry_count,
            timeout_count=left.timeout_count + right.timeout_count,
            consecutive_429_count=max(
                left.consecutive_429_count, right.consecutive_429_count
            ),
            checkpoint_state=right.checkpoint_state or left.checkpoint_state,
            error_message=right.error_message or left.error_message,
            committed_count=left.fetched_count + right.fetched_count,
        )

    def _sleep_after_large_range(
        self,
        result: InvoiceFetchResult,
        direction: str,
        category: str,
    ) -> None:
        cooldown_getter = getattr(self.crawler, 'range_done_cooldown_ms', None)
        if cooldown_getter is None:
            return
        cooldown_ms = cooldown_getter(result)
        if cooldown_ms <= 0:
            return
        logger.info(
            'Overview post-range sleep class=%s direction=%s query_type=%s '
            'pages=%s total=%s cooldown_ms=%s profile=%s',
            result.range_class, direction, category_to_query_type(category), result.pages,
            result.first_page_total, cooldown_ms,
            getattr(getattr(self.crawler, 'paging_options', None), 'profile', 'unknown'),
        )
        time.sleep(cooldown_ms / 1000)

    @staticmethod
    def _format_portal_date(value: object) -> str | None:
        if not value:
            return None
        if isinstance(value, (date, datetime)):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            except ValueError:
                return str(value)
        if isinstance(parsed, datetime) and parsed.tzinfo is not None:
            parsed = parsed.astimezone(BUSINESS_TIMEZONE)
        return parsed.strftime('%d/%m/%Y')

    def _cash_records_to_xlsx(
        self,
        records: Sequence[dict],
        direction: str,
        begin_date: date,
        end_date: date,
    ) -> bytes:
        started = time.perf_counter()
        template_path = self._template_path('cash_register', direction)
        wb = _load_workbook(template_path)
        ws = wb.active
        header_row = _find_header_row(ws)
        prototype_row = header_row + 1
        if ws.max_row < prototype_row:
            ws.insert_rows(prototype_row)

        for index, item in enumerate(records, start=1):
            if direction == 'purchase':
                party_values = [
                    item.get('nbmst'), item.get('nbten'), item.get('nbdchi'),
                    item.get('nmmst'), item.get('nmten'), item.get('nmcmnd'),
                ]
            else:
                party_values = [
                    item.get('nbmst'), item.get('nbten'), item.get('nmmst'),
                    item.get('nmten'), item.get('nmdchi'), item.get('nmcmnd'),
                ]
            values = [
                index, item.get('khmshdon'), item.get('khhdon'), item.get('shdon'),
                self._format_portal_date(item.get('tdlap')),
                *party_values,
                item.get('tgtcthue'), item.get('tgtthue'), item.get('ttcktmai'),
                item.get('tgtttbso'),
                INVOICE_STATUS_LABELS.get(item.get('tthai'), str(item.get('tthai') or '')),
                item.get('kqcht') or 'Cục Thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền',
            ]
            target_row = prototype_row + index - 1
            for col, value in enumerate(values, start=1):
                source = ws.cell(prototype_row, col)
                target = ws.cell(target_row, col)
                if target_row != prototype_row:
                    _clone_cell(source, target)
                target.value = value
        if not records:
            for cell in ws[prototype_row]:
                cell.value = None
        ws.cell(4, 1).value = f'Từ ngày {begin_date:%d/%m/%Y} đến ngày {end_date:%d/%m/%Y}'
        stream = io.BytesIO()
        wb.save(stream)
        wb.close()
        logger.info(
            'Rendered cash-register workbook %s with %d rows in %.2fs',
            direction, len(records), time.perf_counter() - started,
        )
        return stream.getvalue()

    def _electronic_records_to_xlsx(
        self,
        records: Sequence[dict],
        direction: str,
        begin_date: date,
        end_date: date,
    ) -> bytes:
        """Render already-fetched electronic rows without another portal request."""
        started = time.perf_counter()
        template_path = self._template_path('electronic', direction)
        wb = _load_workbook(template_path)
        ws = wb.active
        header_row = _find_header_row(ws)
        prototype_row = header_row + 1
        if ws.max_row < prototype_row:
            ws.insert_rows(prototype_row)

        for index, item in enumerate(records, start=1):
            values = [
                index,
                item.get('khmshdon'),
                item.get('khhdon'),
                item.get('shdon'),
                self._format_portal_date(item.get('tdlap')),
                item.get('nbmst'),
                item.get('nbten'),
                item.get('nmmst'),
                item.get('nmten'),
                item.get('nmdchi'),
                item.get('tgtcthue'),
                item.get('tgtthue'),
                item.get('ttcktmai'),
                item.get('tgtphi'),
                item.get('tgtttbso'),
                item.get('dvtte'),
                item.get('tgia'),
                INVOICE_STATUS_LABELS.get(
                    item.get('tthai'), str(item.get('tthai') or '')
                ),
                item.get('kqcht') or '',
            ]
            target_row = prototype_row + index - 1
            for col, value in enumerate(values, start=1):
                source = ws.cell(prototype_row, col)
                target = ws.cell(target_row, col)
                if target_row != prototype_row:
                    _clone_cell(source, target)
                target.value = value
        if not records:
            for cell in ws[prototype_row]:
                cell.value = None
        ws.cell(4, 1).value = f'Từ ngày {begin_date:%d/%m/%Y} đến ngày {end_date:%d/%m/%Y}'
        stream = io.BytesIO()
        wb.save(stream)
        wb.close()
        logger.info(
            'Rendered electronic workbook %s with %d partial rows in %.2fs',
            direction, len(records), time.perf_counter() - started,
        )
        return stream.getvalue()

    def merge_exports(
        self,
        *,
        source_paths: Sequence[Path],
        target_path: Path,
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
    ) -> DownloadReport:
        started = time.perf_counter()
        template_path = self._template_path(category, direction)
        if not template_path.is_file():
            raise FileNotFoundError(f'Missing Excel template: {template_path}')

        target_wb = _load_workbook(template_path)
        target_ws = target_wb.active
        target_header_row = _find_header_row(target_ws)
        target_headers = [target_ws.cell(target_header_row, col).value for col in range(1, target_ws.max_column + 1)]

        rows: list[list[Cell]] = []
        source_workbooks = []
        for source_path in source_paths:
            source_wb = _load_workbook(source_path, data_only=False)
            source_workbooks.append(source_wb)
            source_ws = source_wb.active
            source_header_row = _find_header_row(source_ws)
            source_headers = [source_ws.cell(source_header_row, col).value for col in range(1, source_ws.max_column + 1)]
            if len(source_headers) != len(target_headers):
                for workbook in source_workbooks:
                    workbook.close()
                target_wb.close()
                raise RuntimeError(
                    f'Excel schema changed for {source_path.name}: '
                    f'expected {len(target_headers)} columns, received {len(source_headers)}'
                )
            if len(source_workbooks) == 1:
                # Purchase/sold labels differ slightly for the same workbook
                # shape. Keep the reference template's layout and styles, but
                # use the exact current labels/title returned by the portal.
                for col, header in enumerate(source_headers, start=1):
                    target_ws.cell(target_header_row, col).value = header
                target_ws.cell(3, 1).value = source_ws.cell(3, 1).value
            for row in source_ws.iter_rows(
                min_row=source_header_row + 1,
                max_row=source_ws.max_row,
                max_col=source_ws.max_column,
            ):
                if any(cell.value is not None for cell in row):
                    rows.append(list(row))

        input_rows = len(rows)
        unique_rows: list[list[Cell]] = []
        seen: set[tuple[object, ...]] = set()
        for row in rows:
            # STT is generated per export and must not take part in deduplication.
            key = tuple(cell.value for cell in row[1:])
            if key in seen:
                continue
            seen.add(key)
            unique_rows.append(row)
        unique_rows.sort(key=lambda row: _date_sort_value(row[4].value), reverse=True)

        data_start = target_header_row + 1
        prototype_styles = [
            copy(target_ws.cell(data_start, col)._style)
            for col in range(1, target_ws.max_column + 1)
        ]
        prototype_height = target_ws.row_dimensions[data_start].height
        if target_ws.max_row >= data_start:
            target_ws.delete_rows(data_start, target_ws.max_row - data_start + 1)
        for index, source_row in enumerate(unique_rows, start=1):
            target_row = data_start + index - 1
            for col, source_cell in enumerate(source_row, start=1):
                target_cell = target_ws.cell(target_row, col)
                if category == 'cash_register':
                    # Cash-register rows use one template style per column.
                    # Reusing destination-workbook style IDs avoids the very
                    # expensive cross-workbook registration for every cell.
                    target_cell.value = source_cell.value
                    target_cell._style = copy(prototype_styles[col - 1])
                else:
                    _clone_cell(source_cell, target_cell)
            target_ws.cell(target_row, 1).value = index
            source_height = (
                prototype_height
                if category == 'cash_register'
                else source_row[0].parent.row_dimensions[source_row[0].row].height
            )
            if source_height is not None:
                target_ws.row_dimensions[target_row].height = source_height

        target_ws.cell(4, 1).value = (
            f'Từ ngày {begin_date:%d/%m/%Y} đến ngày {end_date:%d/%m/%Y}'
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_wb.save(target_path)
        target_wb.close()
        for workbook in source_workbooks:
            workbook.close()

        logger.info(
            'Merged %s/%s: %d input rows -> %d output rows from %d files in %.2fs',
            direction, category, input_rows, len(unique_rows), len(source_paths),
            time.perf_counter() - started,
        )

        return DownloadReport(
            direction=direction,
            category=category,
            output_path=target_path,
            source_files=len(source_paths),
            input_rows=input_rows,
            output_rows=len(unique_rows),
            duplicate_rows=input_rows - len(unique_rows),
        )
