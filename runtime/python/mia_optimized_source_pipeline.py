"""Performance adapter for the vendored sequential source pipeline.

The crawler/service remains source-owned.  This adapter changes only expensive
host-side orchestration that is unnecessary for the local desktop runtime:

* once an overview unit has successfully returned after its durable page and
  checkpoint commits, do not re-read the just-written database/raw pages solely
  to verify them again before advancing to the next month;
* materialize the source detail planner once per job and reuse the exact same
  source decisions for per-month Detail/XML/MVT work instead of rescanning the
  whole invoice range for every count/process pass;
* while that one source planner pass is running, cache the company's existing
  detail rows in memory so the source planner does not open SQLite once per
  invoice key.

No request, pagination, normalization, persistence, fetch/refresh/skip decision,
or artifact operation is reimplemented here.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import sys


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.worker_runtime.pipeline import InvoiceCrawlPipeline


def _detail_key(company_tax_code, direction, query_type, nbmst, khhdon, shdon, khmshdon):
    return (
        str(company_tax_code), str(direction), str(query_type), str(nbmst),
        str(khhdon), str(shdon), str(khmshdon),
    )


class OptimizedInvoiceCrawlPipeline(InvoiceCrawlPipeline):
    """One-worker source pipeline with redundant local verification/scans removed."""

    def run(self, job, worker_id: str, lease_token: str):
        # The pipeline object is reused by the single local supervisor, so all
        # cached planning state is explicitly job-scoped.
        self._desktop_overview_complete = False
        self._desktop_detail_plan = None
        self._desktop_detail_plan_by_month = None
        try:
            return super().run(job, worker_id, lease_token)
        finally:
            self._desktop_overview_complete = False
            self._desktop_detail_plan = None
            self._desktop_detail_plan_by_month = None

    @staticmethod
    def _cached_detail_lookup(original_lookup):
        def lookup(
            repository, company_tax_code, direction, query_type,
            nbmst, khhdon, shdon, khmshdon,
        ):
            cache_owner = getattr(repository, "_mia_desktop_detail_cache_owner", None)
            cache = getattr(repository, "_mia_desktop_detail_cache", None)
            owner = str(company_tax_code)
            if cache is None or cache_owner != owner:
                # CoveragePlanner initializes the schema before entering its
                # item loop. Read the existing company rows through one SQLite
                # connection instead of get_detail_by_invoice_key opening one
                # connection for every overview invoice.
                try:
                    with closing(repository._connect()) as connection:
                        rows = connection.execute(
                            "SELECT * FROM invoice_detail_items WHERE company_tax_code = ?",
                            (owner,),
                        ).fetchall()
                except Exception:
                    # Preserve source behavior if a future repository/schema
                    # change makes the fast lookup unavailable.
                    return original_lookup(
                        repository, company_tax_code, direction, query_type,
                        nbmst, khhdon, shdon, khmshdon,
                    )
                cache = {
                    _detail_key(
                        row["company_tax_code"], row["direction"], row["query_type"],
                        row["nbmst"], row["khhdon"], row["shdon"], row["khmshdon"],
                    ): dict(row)
                    for row in rows
                }
                repository._mia_desktop_detail_cache_owner = owner
                repository._mia_desktop_detail_cache = cache
            return cache.get(_detail_key(
                company_tax_code, direction, query_type,
                nbmst, khhdon, shdon, khmshdon,
            ))

        return lookup

    def _ensure_detail_plan(self, job, parameters, coverage):
        cached = self._desktop_detail_plan
        if cached is not None:
            return cached

        # Call the source implementation directly. It remains authoritative for
        # every fetch/refresh/skip decision. The temporary lookup replacement
        # only removes thousands of connection-open/query/close cycles while
        # the source CoveragePlanner performs those same decisions.
        original_lookup = InvoiceDetailRepository.get_detail_by_invoice_key
        InvoiceDetailRepository.get_detail_by_invoice_key = self._cached_detail_lookup(
            original_lookup
        )
        try:
            decisions = tuple(
                super()._iter_detail_plan(job, parameters, coverage)
            )
        finally:
            InvoiceDetailRepository.get_detail_by_invoice_key = original_lookup

        grouped = defaultdict(list)
        for decision in decisions:
            invoice_date = str(decision.item.get("nlap_date") or "")
            month_key = invoice_date[:7]
            if month_key:
                grouped[month_key].append(decision)

        self._desktop_detail_plan = decisions
        self._desktop_detail_plan_by_month = {
            key: tuple(items) for key, items in grouped.items()
        }
        return decisions

    def _iter_detail_plan(self, job, parameters, coverage):
        # Source calls this before authentication only to answer "do we need the
        # source portal?". If any overview slice must be refreshed, the answer
        # is already yes, so a full SQLite/file detail scan at this point is
        # pure overhead. Build the detail plan after overview has refreshed the
        # durable invoice list instead.
        if not self._desktop_overview_complete:
            if any(decision.needs_refresh for decision in coverage.decisions):
                return
            # With a fully stable overview, this scan is necessary to determine
            # whether missing/recent details require authentication. Cache the
            # complete source plan now so later stages do not scan again.
            yield from self._ensure_detail_plan(job, parameters, coverage)
            return

        yield from self._ensure_detail_plan(job, parameters, coverage)

    def _month_detail_plan(self, job, parameters, coverage, month):
        self._ensure_detail_plan(job, parameters, coverage)
        month_key = str(month.get("key") or month.get("from_date") or "")[:7]
        yield from (self._desktop_detail_plan_by_month or {}).get(month_key, ())

    def _run_overview(self, job, parameters, coverage):
        storage = self.planner.storage
        verifier = storage.verify_finalized_overview_range

        # run_overview_unit already returns only after each page/checkpoint has
        # been durably committed. The source pipeline then immediately rereads
        # those same files/rows only as a defensive verification pass. For the
        # local desktop fast path the user explicitly prefers moving straight to
        # the next month after the normal durable commit succeeds.
        storage.verify_finalized_overview_range = lambda **_kwargs: True
        try:
            return super()._run_overview(job, parameters, coverage)
        finally:
            storage.verify_finalized_overview_range = verifier
            self._desktop_overview_complete = True
            # If a future source change happened to build a pre-overview plan
            # while refreshing overview data, never retain stale decisions.
            if any(decision.needs_refresh for decision in coverage.decisions):
                self._desktop_detail_plan = None
                self._desktop_detail_plan_by_month = None


__all__ = ["OptimizedInvoiceCrawlPipeline"]
