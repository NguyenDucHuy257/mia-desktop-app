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
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import logging
import sqlite3
import sys
import time


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.worker_runtime.pipeline import InvoiceCrawlPipeline


logger = logging.getLogger("mia.desktop_source_pipeline")


def prepare_invoice_database(database_path: Path | str) -> None:
    """Upgrade an existing company DB and enable reader/writer coexistence."""
    database_path = Path(database_path)
    InvoiceOverviewRepository(database_path).init_db()
    InvoiceDetailRepository(database_path).init_db()
    InvoicePackageRepository(database_path).init_db()
    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        # Results/search are read concurrently with the one sequential source
        # writer. WAL prevents those bounded reads from blocking crawler
        # commits; NORMAL retains SQLite's WAL durability contract without a
        # full fsync for every small transaction.
        connection.execute("PRAGMA journal_mode = WAL").fetchone()
        connection.execute("PRAGMA synchronous = NORMAL")


def _detail_key(company_tax_code, direction, query_type, nbmst, khhdon, shdon, khmshdon):
    return (
        str(company_tax_code), str(direction), str(query_type), str(nbmst),
        str(khhdon), str(shdon), str(khmshdon),
    )


class OptimizedInvoiceCrawlPipeline(InvoiceCrawlPipeline):
    """One-worker source pipeline with redundant local verification/scans removed."""

    SOURCE_TIMEOUT_RETRY_BASE_SECONDS = 15.0
    SOURCE_TIMEOUT_RETRY_MAX_SECONDS = 60.0

    def _latest_month_force_slices(self, parameters):
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
        for month_begin, month_end in ((previous_begin, previous_end), (current_begin, current_end)):
            begin, end = max(request_from, month_begin), min(request_to, month_end)
            if begin > end:
                continue
            for direction in parameters["directions"]:
                for query_type in parameters["query_types"]:
                    slices.add((direction, query_type, begin, end))
        return frozenset(slices)

    def run(self, job, worker_id: str, lease_token: str):
        database_path = (
            Path(self.planner.data_root) / job.company_tax_code
            / "db" / "invoices.sqlite3"
        )
        try:
            prepare_invoice_database(database_path)
            job = self._prepare_full_replacement(job)
        except Exception as error:
            logger.exception(
                "desktop_source_database_prepare_failed job_id=%s error_type=%s",
                job.job_id, type(error).__name__,
            )
            raise
        # The pipeline object is reused by the single local supervisor, so all
        # cached planning/presentation state is explicitly job-scoped.
        self._desktop_overview_complete = False
        self._desktop_detail_plan = None
        self._desktop_detail_plan_by_month = None
        self._desktop_current_unit = None
        originals = self._install_unit_progress_wrappers()
        timeout_attempt = 0
        try:
            while True:
                try:
                    return super().run(job, worker_id, lease_token)
                except Exception as error:
                    if str(getattr(error, "code", "")) != "source_timeout":
                        # Preserve source classification while retaining the
                        # stack needed to diagnose non-transient failures.
                        logger.exception(
                            "desktop_source_pipeline_failed job_id=%s error_type=%s",
                            job.job_id, type(error).__name__,
                        )
                        raise
                    timeout_attempt += 1
                    delay = min(
                        self.SOURCE_TIMEOUT_RETRY_BASE_SECONDS
                        * (2 ** min(timeout_attempt - 1, 2)),
                        self.SOURCE_TIMEOUT_RETRY_MAX_SECONDS,
                    )
                    logger.warning(
                        "desktop_source_timeout_wait job_id=%s attempt=%s retry_in_seconds=%s",
                        job.job_id, timeout_attempt, int(delay),
                    )
                    self._wait_before_source_retry(job.job_id, delay)
                    # Reload durable progress/checkpoints before restarting.
                    # This resumes the missing unit instead of replaying rows
                    # that the source already committed before timing out.
                    job = self.repository.get_job(job.job_id)
                    self._desktop_overview_complete = False
                    self._desktop_detail_plan = None
                    self._desktop_detail_plan_by_month = None
                    self._desktop_current_unit = None
        finally:
            for name, original in originals.items():
                setattr(self.core, name, original)
            self._desktop_overview_complete = False
            self._desktop_detail_plan = None
            self._desktop_detail_plan_by_month = None
            self._desktop_current_unit = None

    def _wait_before_source_retry(self, job_id: str, delay_seconds: float) -> None:
        """Wait for the portal without making cancellation wait for backoff."""
        deadline = time.monotonic() + max(0.0, delay_seconds)
        while True:
            self._check_interrupted(job_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.25, remaining))

    def _prepare_full_replacement(self, job):
        parameters = dict(job.parameters)
        if parameters.get("sync_mode") != "new" or parameters.get("replacement_prepared"):
            return job
        database_path = Path(self.planner.data_root) / job.company_tax_code / "db" / "invoices.sqlite3"
        InvoiceOverviewRepository(database_path).init_db()
        InvoiceDetailRepository(database_path).init_db()
        InvoicePackageRepository(database_path).init_db()
        date_from, date_to = str(parameters["date_from"]), str(parameters["date_to"])
        directions = list(parameters.get("directions") or ())
        if len(directions) != 1:
            raise ValueError("new sync replacement requires exactly one direction")
        direction = str(directions[0])
        replaced = parameters.get("replaced_old_count")
        if replaced is None:
            with closing(sqlite3.connect(database_path, timeout=30)) as connection:
                replaced = int(connection.execute(
                    """SELECT COUNT(*) FROM invoice_overview_items
                       WHERE company_tax_code=? AND direction=? AND nlap_date BETWEEN ? AND ?""",
                    (job.company_tax_code, direction, date_from, date_to),
                ).fetchone()[0])
            job = self.repository.merge_job_parameters(job.job_id, {
                "replaced_old_count": replaced, "replacement_prepared": False,
            })
        # The source Overview repository already stages a forced refresh and
        # atomically activates it only after every status partition completes.
        # Do not delete active Overview/Detail rows before that safe commit.
        return self.repository.merge_job_parameters(job.job_id, {
            "replaced_old_count": int(replaced), "replacement_prepared": True,
        })

    @staticmethod
    def _delete_replacement_range(*, database_path, company_tax_code, direction, date_from, date_to):
        with closing(sqlite3.connect(database_path, timeout=30)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            with connection:
                keys = (company_tax_code, direction, date_from, date_to)
                detail_ids = [row[0] for row in connection.execute(
                    """SELECT id FROM invoice_detail_items WHERE company_tax_code=? AND direction=?
                       AND nlap_date BETWEEN ? AND ?""", keys
                ).fetchall()]
                if detail_ids:
                    placeholders = ",".join("?" for _ in detail_ids)
                    connection.execute(f"DELETE FROM invoice_detail_lines WHERE detail_item_id IN ({placeholders})", detail_ids)
                    connection.execute(f"DELETE FROM invoice_detail_items WHERE id IN ({placeholders})", detail_ids)
                connection.execute(
                    """DELETE FROM invoice_package_items WHERE company_tax_code=? AND direction=?
                       AND nlap_date BETWEEN ? AND ?""", keys
                )
                overview_ids = [row[0] for row in connection.execute(
                    """SELECT id FROM invoice_overview_items WHERE company_tax_code=? AND direction=?
                       AND nlap_date BETWEEN ? AND ?""", keys
                ).fetchall()]
                if overview_ids:
                    placeholders = ",".join("?" for _ in overview_ids)
                    connection.execute(f"DELETE FROM invoice_overview_attributes WHERE invoice_item_id IN ({placeholders})", overview_ids)
                    connection.execute(f"DELETE FROM invoice_overview_items WHERE id IN ({placeholders})", overview_ids)
                connection.execute(
                    """UPDATE invoice_overview_checkpoints SET checkpoint_status='incomplete'
                       WHERE company_tax_code=? AND direction=? AND NOT (to_date < ? OR from_date > ?)""",
                    (company_tax_code, direction, date_from, date_to),
                )

    @staticmethod
    def _overview_payload(parameters, direction, query_type, month):
        payload = InvoiceCrawlPipeline._overview_payload(parameters, direction, query_type, month)
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

            def wrapped(
                job, payload, *args, _original=original, _stage=stage,
                _name=name, **kwargs,
            ):
                if _stage == "ensure_xml" and isinstance(payload, dict):
                    # The source package response contains both invoice.xml and
                    # invoice.html. Ask the existing source handler/storage
                    # service to persist both from that one package request.
                    # This changes no portal, retry, cache or naming rule.
                    payload = {**payload, "export_xml": True, "export_html": True}
                self._set_current_source_unit(_stage, payload)
                # prepare_overview_unit is a lightweight count preflight and
                # deliberately has no progress_callback parameter. Only the
                # actual overview download accepts the storage/enrichment
                # callback. Passing it to both caused every fresh Overview job
                # to fail before the first portal data request.
                if _name == "run_overview_unit" and "progress_callback" not in kwargs:
                    def overview_progress(event, current=None, total=None):
                        self._state["message"] = f"overview:{event}"
                        if str(event).startswith("taxable_total_") and current is not None:
                            self._state["overview_post_processed"] = int(current)
                        if str(event).startswith("taxable_total_") and total is not None:
                            self._state["overview_post_total"] = int(total)
                        self._persist(force=True)
                    kwargs["progress_callback"] = overview_progress
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
            plan_parameters = {**parameters, "force_refresh": False}
        try:
            decisions = tuple(
                super()._iter_detail_plan(job, plan_parameters, coverage)
            )
        finally:
            InvoiceDetailRepository.get_detail_by_invoice_key = original_lookup

        refreshed_slices = {
            (item.direction, item.query_type, item.from_date.isoformat()[:7])
            for item in coverage.decisions if item.needs_refresh
        }
        decisions = tuple(
            replace(decision, force_refresh=True, action="refresh")
            if (
                str(decision.item.get("direction")),
                str(decision.item.get("query_type")),
                str(decision.item.get("nlap_date") or "")[:7],
            ) in refreshed_slices else decision
            for decision in decisions
        )

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
        detail_repository = InvoiceDetailRepository(
            Path(self.planner.data_root) / job.company_tax_code / "db" / "invoices.sqlite3"
        )
        for decision in coverage.decisions:
            if decision.needs_refresh:
                detail_repository.invalidate_detail_checkpoint(
                    company_tax_code=job.company_tax_code,
                    direction=decision.direction, query_type=decision.query_type,
                    from_date=decision.from_date.isoformat(),
                    to_date=decision.to_date.isoformat(), job_id=job.job_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

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

    def _run_detail(self, job, parameters, coverage):
        database_path = Path(self.planner.data_root) / job.company_tax_code / "db" / "invoices.sqlite3"
        repository = InvoiceDetailRepository(database_path)
        timestamp = datetime.now(timezone.utc).isoformat()
        routes = []
        for month in self._module_months("detail"):
            for direction in parameters["directions"]:
                for query_type in parameters["query_types"]:
                    route = (direction, query_type, month["from_date"], month["to_date"])
                    routes.append(route)
                    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
                        expected = int(connection.execute(
                            """SELECT COUNT(*) FROM invoice_overview_items
                               WHERE company_tax_code=? AND direction=? AND query_type=?
                                 AND nlap_date BETWEEN ? AND ?""",
                            (job.company_tax_code, *route),
                        ).fetchone()[0])
                    repository.begin_detail_checkpoint(
                        company_tax_code=job.company_tax_code, direction=direction,
                        query_type=query_type, from_date=month["from_date"],
                        to_date=month["to_date"], overview_expected=expected,
                        job_id=job.job_id, timestamp=timestamp,
                    )
        try:
            result = super()._run_detail(job, parameters, coverage)
            incomplete = []
            for direction, query_type, from_date, to_date in routes:
                outcome = repository.finish_detail_checkpoint(
                    company_tax_code=job.company_tax_code, direction=direction,
                    query_type=query_type, from_date=from_date, to_date=to_date,
                    job_id=job.job_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                if outcome["status"] != "finalized":
                    incomplete.append((direction, query_type, from_date, to_date))
            if incomplete:
                raise RuntimeError("detail coverage incomplete after persisted verification")
            return result
        except Exception as error:
            terminal = "cancelled" if "cancel" in type(error).__name__.casefold() else "failed"
            repository.mark_detail_checkpoint_status(
                company_tax_code=job.company_tax_code, job_id=job.job_id,
                status=terminal, timestamp=datetime.now(timezone.utc).isoformat(),
            )
            raise


__all__ = ["OptimizedInvoiceCrawlPipeline", "prepare_invoice_database"]
