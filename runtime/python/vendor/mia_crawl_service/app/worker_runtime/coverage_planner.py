from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path

from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.services.invoice_overview_storage_service import InvoiceOverviewStorageService
from app.services.overview_downloader import ELECTRONIC_STATUSES
from app.utils.date_utils import BUSINESS_TIMEZONE, split_by_calendar_month


FRESHNESS_DAYS = 30


@dataclass(frozen=True)
class CoverageDecision:
    direction: str
    query_type: str
    status_filter: str
    from_date: date
    to_date: date
    classification: str
    planned_items: int = 0

    @property
    def needs_refresh(self) -> bool:
        return self.classification != 'stable_finalized_skip'


@dataclass(frozen=True)
class CoveragePlan:
    cutoff_date: date
    decisions: tuple[CoverageDecision, ...]

    @property
    def overview_slices(self) -> tuple[tuple[str, str, date, date], ...]:
        return tuple(dict.fromkeys(
            (item.direction, item.query_type, item.from_date, item.to_date)
            for item in self.decisions if item.needs_refresh
        ))

    @property
    def skipped_count(self) -> int:
        return sum(
            item.classification == 'stable_finalized_skip' for item in self.decisions
        )

    def summary(self) -> dict[str, int | str]:
        counts: dict[str, int | str] = {'freshness_cutoff': self.cutoff_date.isoformat()}
        for item in self.decisions:
            counts[item.classification] = int(counts.get(item.classification, 0)) + 1
        return counts


@dataclass(frozen=True)
class DetailDecision:
    item: dict
    force_refresh: bool
    action: str = 'fetch'


class CoveragePlanner:
    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)
        self.storage = InvoiceOverviewStorageService(self.data_root)

    def plan(
        self,
        *,
        company_tax_code: str,
        date_from: date,
        date_to: date,
        directions: list[str],
        query_types: list[str],
        business_now: datetime,
        force_refresh: bool = False,
        force_range: tuple[date, date] | None = None,
        force_slices: frozenset[tuple[str, str, date, date]] = frozenset(),
    ) -> CoveragePlan:
        if business_now.tzinfo is None:
            raise ValueError('business_now must be timezone-aware')
        local_now = business_now.astimezone(BUSINESS_TIMEZONE)
        cutoff_date = (local_now - timedelta(days=FRESHNESS_DAYS)).date()
        repository = InvoiceOverviewRepository(
            self.data_root / company_tax_code / 'db' / 'invoices.sqlite3'
        )
        # A first job for a new account has no company-scoped invoice database
        # yet. Planning is a read operation, but its schema must exist before
        # querying checkpoints; otherwise public job creation fails with a
        # SQLite "no such table" OperationalError.
        repository.init_db()
        decisions: list[CoverageDecision] = []
        for begin, end in split_by_calendar_month(date_from, date_to):
            for direction in directions:
                for query_type in query_types:
                    statuses = ELECTRONIC_STATUSES if query_type == 'query' else (None,)
                    forced = force_refresh or (
                        direction, query_type, begin, end
                    ) in force_slices
                    for status in statuses:
                        status_filter = 'all' if status is None else str(status)
                        checkpoint = repository.get_overview_checkpoint(
                            company_tax_code=company_tax_code,
                            direction=direction,
                            query_type=query_type,
                            from_date=begin.isoformat(),
                            to_date=end.isoformat(),
                            status_filter=status_filter,
                        )
                        classification = self._classify(
                            checkpoint=checkpoint,
                            forced=forced,
                            realtime=end >= cutoff_date,
                            company_tax_code=company_tax_code,
                            direction=direction,
                            query_type=query_type,
                            begin=begin,
                            end=end,
                            status=status,
                        )
                        decisions.append(CoverageDecision(
                            direction, query_type, status_filter,
                            begin, end, classification,
                            int(checkpoint.get('expected_total') or 0)
                            if checkpoint else 0,
                        ))
        return CoveragePlan(cutoff_date, tuple(decisions))

    def detail_work(
        self,
        *,
        company_tax_code: str,
        date_from: date,
        date_to: date,
        directions: list[str],
        query_types: list[str],
        cutoff_date: date,
        force_refresh: bool = False,
        force_range: tuple[date, date] | None = None,
    ) -> tuple[DetailDecision, ...]:
        return tuple(
            decision for decision in self.plan_details(
                company_tax_code=company_tax_code, date_from=date_from,
                date_to=date_to, directions=directions, query_types=query_types,
                cutoff_date=cutoff_date, force_refresh=force_refresh,
                force_range=force_range,
            ) if decision.action != 'skip_verified'
        )

    def plan_details(
        self,
        *, company_tax_code: str, date_from: date, date_to: date,
        directions: list[str], query_types: list[str], cutoff_date: date,
        force_refresh: bool = False,
        force_range: tuple[date, date] | None = None,
    ) -> tuple[DetailDecision, ...]:
        return tuple(self.iter_detail_decisions(
            company_tax_code=company_tax_code, date_from=date_from,
            date_to=date_to, directions=directions, query_types=query_types,
            cutoff_date=cutoff_date, force_refresh=force_refresh,
            force_range=force_range,
        ))

    def iter_detail_decisions(
        self, *, company_tax_code: str, date_from: date, date_to: date,
        directions: list[str], query_types: list[str], cutoff_date: date,
        force_refresh: bool = False,
        force_range: tuple[date, date] | None = None,
    ):
        database_path = self.data_root / company_tax_code / 'db' / 'invoices.sqlite3'
        overview = InvoiceOverviewRepository(database_path)
        detail = InvoiceDetailRepository(database_path)
        overview.init_db()
        detail.init_db()
        for direction in directions:
            for query_type in query_types:
                for batch in overview.iter_items_for_detail_task_generation(
                    company_tax_code,
                    direction,
                    query_type,
                    date_from.isoformat(),
                    date_to.isoformat(),
                    include_completed=True,
                ):
                    for item in batch:
                        invoice_date = date.fromisoformat(item['nlap_date'])
                        recent = invoice_date >= cutoff_date
                        existing = detail.get_detail_by_invoice_key(
                            company_tax_code, direction, query_type,
                            item['nbmst'], item['khhdon'], item['shdon'],
                            item['khmshdon'],
                        )
                        forced = force_refresh or recent or (
                            force_range is not None
                            and force_range[0] <= invoice_date <= force_range[1]
                        )
                        valid = self._valid_detail(existing)
                        action = (
                            'refresh' if forced else
                            'skip_verified' if valid else 'fetch'
                        )
                        yield DetailDecision(
                            item=item, force_refresh=forced, action=action,
                        )

    @staticmethod
    def _valid_detail(existing: dict | None) -> bool:
        if not existing or existing.get('error_message'):
            return False
        if existing.get('normalized_ready'):
            return True
        raw_path = existing.get('raw_detail_path')
        if not raw_path:
            return False
        path = Path(raw_path)
        try:
            with path.open('r', encoding='utf-8') as stream:
                value = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return isinstance(value, (dict, list))

    def _classify(
        self,
        *,
        checkpoint,
        forced: bool,
        realtime: bool,
        company_tax_code: str,
        direction: str,
        query_type: str,
        begin: date,
        end: date,
        status: int | None,
    ) -> str:
        if forced:
            return 'realtime_refresh'
        if realtime:
            return 'realtime_refresh'
        if checkpoint is None:
            return 'missing'
        checkpoint_status = str(checkpoint.get('checkpoint_status') or '')
        if checkpoint_status in {'in_progress', 'incomplete', 'cancelled'}:
            return 'incomplete'
        if checkpoint_status != 'finalized':
            return 'invalidated'
        if not self.storage.verify_finalized_overview_range(
            company_tax_code=company_tax_code,
            direction=direction,
            query_type=query_type,
            from_date=begin.isoformat(),
            to_date=end.isoformat(),
            status=status,
        ):
            return 'verification_failed'
        return 'stable_finalized_skip'