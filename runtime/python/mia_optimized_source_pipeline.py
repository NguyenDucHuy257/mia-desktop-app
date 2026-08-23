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
  invoice key;
* expose the exact direction/query unit currently being executed so desktop can
  present whether the source worker is on purchase or sold data without
  inventing progress counters.

No request, pagination, normalization, persistence, fetch/refresh/skip decision,
or artifact operation is reimplemented here.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.worker_runtime.pipeline import InvoiceCrawlPipeline


def _detail_key(company_tax_code, direction, query_type, nbmst, khhdon, shdon, khmshdon):
    return (
        str(company_tax_code), str(direction), str(query_type), str(nbmst),
        str(khhdon), str(shdon), str(khmshdon),
    )


class OptimizedInvoiceCrawlPipeline(InvoiceCrawlPipeline):
    """One-worker source pipeline with redundant local verification/scans removed."""

    def _latest_month_force_slices(self, parameters):
        """Force only the two mutable calendar months for supplement jobs.

        The source planner remains authoritative for missing/finalized coverage.
        This desktop policy merely prevents a finalized previous/current month
        from being skipped; it does not force an arbitrary latest requested
        month when the selected range is entirely historical.
        """
        if parameters.get("sync_mode") != "supplement":
            return super()._latest_month_force_slices(parameters)

        request_from = date.fromisoformat(parameters["date_from"])
        request_to = date.fromisoformat(parameters["date_to"])
        current_begin = self.clock().date().replace(day=1)
        previous_end = current_begin - timedelta(days=1)
        previous_begin = previous_end.replace(day=1)
        next_month = (current_begin.replace(day=28) + timedelta(days=4)).replace(day=1)
        current_end = next_month - timedelta(days=1)

        slices = set()
        for month_begin, month_end in (
            (previous_begin, previous_end),
            (current_begin, current_end),
        ):
            begin = max(request_from, month_begin)
            end = min(request_to, month_end)
            if begin > end:
                continue
            for direction in parameters["directions"]:
                for query_type in parameters["query_types"]:
                    slices.add((direction, query_type, begin, end))
        return frozenset(slices)

    def run(self, job, worker_id: str, lease_token: str):
        job = self._prepare_full_replacement(job)
        # The pipeline object is reused by the single local supervisor, so all
        # cached planning/presentation state is explicitly job-scoped.
        self._desktop_overview_complete = False
        self._desktop_detail_plan = None
        self._desktop_detail_plan_by_month = None
        self._desktop_current_unit = None
        overview_commit = None
        if job.parameters.get("sync_mode") == "new":
            overview_commit = InvoiceOverviewRepository.commit_overview_refresh_page

            def publish_refresh_page(repository, *args, **kwargs):
                return self._publish_replacement_page(
                    overview_commit, repository, *args, **kwargs
                )

            InvoiceOverviewRepository.commit_overview_refresh_page = publish_refresh_page
        originals = self._install_unit_progress_wrappers()
        try:
            return super().run(job, worker_id, lease_token)
        finally:
            if overview_commit is not None:
                InvoiceOverviewRepository.commit_overview_refresh_page = overview_commit
            for name, original in originals.items():
                setattr(self.core, name, original)
            self._desktop_overview_complete = False
            self._desktop_detail_plan = None
            self._desktop_detail_plan_by_month = None
            self._desktop_current_unit = None

    @staticmethod
    def _publish_replacement_page(original_commit, repository, *args, **kwargs):
        committed = original_commit(repository, *args, **kwargs)
        # Source staging/checkpoints remain authoritative. Publish the same
        # normalized page only after that durable commit succeeds so active DB
        # counts reflect successfully persisted invoices, not crawler progress.
        repository.upsert_items(
            company_tax_code=kwargs["company_tax_code"],
            direction=kwargs["direction"],
            query_type=kwargs["query_type"],
            invoice_category=kwargs["invoice_category"],
            raw_json_path=kwargs.get("raw_json_path") or "",
            items=kwargs["items"],
            timestamp=kwargs["timestamp"],
        )
        return committed

    def _prepare_full_replacement(self, job):
        parameters = dict(job.parameters)
        if parameters.get("sync_mode") != "new" or parameters.get("replacement_prepared"):
            return job
        database_path = (
            Path(self.planner.data_root) / job.company_tax_code / "db" / "invoices.sqlite3"
        )
        InvoiceOverviewRepository(database_path).init_db()
        InvoiceDetailRepository(database_path).init_db()
        InvoicePackageRepository(database_path).init_db()
        date_from = str(parameters["date_from"])
        date_to = str(parameters["date_to"])
        directions = list(parameters.get("directions") or ())
        if len(directions) != 1:
            raise ValueError("new sync replacement requires exactly one direction")
        direction = str(directions[0])

        replaced = parameters.get("replaced_old_count")
        if replaced is None:
            with closing(sqlite3.connect(database_path, timeout=30)) as connection:
                replaced = int(connection.execute(
                    """SELECT COUNT(*) FROM invoice_overview_items
                       WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ?""",
                    (job.company_tax_code, direction, date_from, date_to),
                ).fetchone()[0])
            job = self.repository.merge_job_parameters(job.job_id, {
                "replaced_old_count": replaced,
                "replacement_prepared": False,
            })

        self._delete_replacement_range(
            database_path=database_path,
            company_tax_code=job.company_tax_code,
            direction=direction,
            date_from=date_from,
            date_to=date_to,
        )
        return self.repository.merge_job_parameters(job.job_id, {
            "replaced_old_count": int(replaced),
            "replacement_prepared": True,
        })

    @staticmethod
    def _delete_replacement_range(
        *, database_path, company_tax_code, direction, date_from, date_to,
    ):
        """Delete only one company/direction/range; partial reloads are retained."""
        with closing(sqlite3.connect(database_path, timeout=30)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            with connection:
                keys = (company_tax_code, direction, date_from, date_to)
                detail_ids = [row[0] for row in connection.execute(
                    """SELECT id FROM invoice_detail_items
                       WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ?""", keys
                ).fetchall()]
                if detail_ids:
                    placeholders = ",".join("?" for _ in detail_ids)
                    connection.execute(
                        f"DELETE FROM invoice_detail_lines WHERE detail_item_id IN ({placeholders})",
                        detail_ids,
                    )
                    connection.execute(
                        f"DELETE FROM invoice_detail_items WHERE id IN ({placeholders})",
                        detail_ids,
                    )
                connection.execute(
                    """DELETE FROM invoice_package_items
                       WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ?""", keys
                )
                overview_ids = [row[0] for row in connection.execute(
                    """SELECT id FROM invoice_overview_items
                       WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ?""", keys
                ).fetchall()]
                if overview_ids:
                    placeholders = ",".join("?" for _ in overview_ids)
                    connection.execute(
                        f"DELETE FROM invoice_overview_attributes WHERE invoice_item_id IN ({placeholders})",
                        overview_ids,
                    )
                    connection.execute(
                        f"DELETE FROM invoice_overview_items WHERE id IN ({placeholders})",
                        overview_ids,
                    )
                connection.execute(
                    """UPDATE invoice_overview_checkpoints
                       SET checkpoint_status='incomplete'
                       WHERE company_tax_code=? AND direction=?
                         AND NOT (to_date < ? OR from_date > ?)""",
                    (company_tax_code, direction, date_from, date_to),
                )

    @staticmethod
    def _overview_payload(parameters, direction, query_type, month):
        payload = InvoiceCrawlPipeline._overview_payload(
            parameters, direction, query_type, month
        )
        if parameters.get("sync_mode") == "supplement":
            payload["sync_mode"] = "supplement"
        return payload

    def _install_unit_progress_wrappers(self):
        originals = {}
        for name, stage in (
            ("prepare_overview_unit", "overview"),
            ("run_overview_unit", "overview"),
            ("run_detail_unit", "detail"),
            ("run_xml_unit", "ensure_xml"),
            ("run_mvt_scope", "mvt"),
        ):
            original = getattr(self.core, name, None)
            if original is None:
                continue
            originals[name] = original

            def wrapped(job, payload, *args, _original=original, _stage=stage, **kwargs):
                if _stage == "ensure_xml" and isinstance(payload, dict):
                    # The source package response contains both invoice.xml and
                    # invoice.html. Ask the existing source handler/storage
                    # service to persist both from that one package request.
                    # This changes no portal, retry, cache or naming rule.
                    payload = {**payload, "export_xml": True, "export_html": True}
                self._set_current_source_unit(_stage, payload)
                try:
                    outcome = _original(job, payload, *args, **kwargs)
                except Exception:
                    # Preserve the source retry/lease decision. The current item
                    # remains running; the terminal job status lets the desktop
                    # present it as failed only if the source gives up.
                    raise
                if _stage == "ensure_xml":
                    state = (
                        "failed"
                        if isinstance(outcome, dict) and outcome.get("outcome") == "unavailable"
                        else "completed"
                    )
                    self._complete_current_artifact(payload, state)
                return outcome

            setattr(self.core, name, wrapped)
        return originals

    def _set_current_source_unit(self, stage, payload):
        if not isinstance(payload, dict) or not hasattr(self, "_state"):
            return
        direction = payload.get("direction")
        query_type = payload.get("query_type")
        if direction not in {"purchase", "sold"}:
            return
        unit = (stage, str(direction), str(query_type or ""))
        current_artifact = None
        if stage == "ensure_xml":
            current_artifact = {
                "direction": str(direction),
                "query_type": str(query_type or ""),
                "nbmst": str(payload.get("nbmst") or ""),
                "khhdon": str(payload.get("khhdon") or ""),
                "shdon": str(payload.get("shdon") or ""),
                "khmshdon": str(payload.get("khmshdon") or ""),
            }
        if (
            unit == self._desktop_current_unit
            and (current_artifact is None or self._state.get("current_artifact") == current_artifact)
        ):
            return
        self._desktop_current_unit = unit
        self._state["current_direction"] = str(direction)
        self._state["current_query_type"] = str(query_type) if query_type else None
        if current_artifact is not None:
            self._state["current_artifact"] = current_artifact
            progress = self._state.setdefault("artifact_progress", {"items": {}})
            items = progress.setdefault("items", {})
            key = self._artifact_key(current_artifact)
            items[key] = {"xml": "running", "html": "running"}
            progress["current_key"] = key
        # Persist only when the source changes logical unit. Item/month progress
        # continues to use the source pipeline's own persistence throttle.
        self._persist(force=True)

    @staticmethod
    def _artifact_key(payload):
        return "|".join(str(payload.get(name) or "") for name in (
            "direction", "query_type", "nbmst", "khhdon", "shdon", "khmshdon",
        ))

    def _complete_current_artifact(self, payload, state):
        if not isinstance(payload, dict) or not hasattr(self, "_state"):
            return
        progress = self._state.setdefault("artifact_progress", {"items": {}})
        items = progress.setdefault("items", {})
        key = self._artifact_key(payload)
        previous = items.get(key) or {}
        items[key] = {"xml": state, "html": state}
        progress["current_key"] = key
        if previous.get("xml") not in {"completed", "failed"}:
            progress["processed"] = int(progress.get("processed") or 0) + 1
        if state == "completed" and previous.get("xml") != "completed":
            progress["completed_xml"] = int(progress.get("completed_xml") or 0) + 1
            progress["completed_html"] = int(progress.get("completed_html") or 0) + 1
        self._persist(force=True)

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
        plan_parameters = parameters
        if parameters.get("sync_mode") == "supplement":
            # Supplement refreshes the Overview range but fetches Detail only
            # for missing/recent invoices; existing detail rows stay intact.
            plan_parameters = {**parameters, "force_refresh": False}
        try:
            decisions = tuple(
                super()._iter_detail_plan(job, plan_parameters, coverage)
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
