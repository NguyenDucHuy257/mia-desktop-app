import unittest
from datetime import date, datetime, timedelta, timezone
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mia_optimized_source_pipeline import OptimizedInvoiceCrawlPipeline
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.pipeline import InvoiceCrawlPipeline


class _Planner:
    def __init__(self, decisions):
        self.decisions = tuple(decisions)
        self.calls = 0

    def iter_detail_decisions(self, **_kwargs):
        self.calls += 1
        yield from self.decisions

    def plan_details(self, **kwargs):
        return tuple(self.iter_detail_decisions(**kwargs))


class OptimizedSourcePipelineTests(unittest.TestCase):
    def _pipeline(self, planner):
        pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
        pipeline.planner = planner
        pipeline._desktop_overview_complete = False
        pipeline._desktop_detail_plan = None
        pipeline._desktop_detail_plan_by_month = None
        return pipeline

    @staticmethod
    def _parameters():
        return {
            "date_from": "2025-05-01",
            "date_to": "2025-07-31",
            "directions": ["purchase", "sold"],
            "query_types": ["query", "sco-query"],
            "force_refresh": False,
            "refresh_latest_month": False,
        }

    def test_refreshing_overview_does_not_scan_details_before_auth(self):
        planner = _Planner([])
        pipeline = self._pipeline(planner)
        coverage = SimpleNamespace(
            cutoff_date=date(2025, 7, 1),
            decisions=(SimpleNamespace(needs_refresh=True),),
        )
        job = SimpleNamespace(company_tax_code="0100000000")

        self.assertEqual(
            list(pipeline._iter_detail_plan(job, self._parameters(), coverage)),
            [],
        )
        self.assertEqual(planner.calls, 0)

    def test_supplement_mode_is_propagated_to_overview_units(self):
        payload = OptimizedInvoiceCrawlPipeline._overview_payload(
            {"session_hash": "session", "sync_mode": "supplement"},
            "purchase", "query",
            {"from_date": "2025-05-01", "to_date": "2025-05-31"},
        )
        self.assertEqual(payload["sync_mode"], "supplement")
        self.assertTrue(payload["restart_coverage"])

    @staticmethod
    def _business_now(year=2026, month=8, day=23):
        return datetime(year, month, day, 10, tzinfo=timezone(timedelta(hours=7)))

    def _coverage_actions(self, *, mode, synced, directions=("purchase",), date_from="2026-01-01", date_to="2026-08-31", now=None):
        now = now or self._business_now()
        parameters = {
            "date_from": date_from,
            "date_to": date_to,
            "directions": list(directions),
            "query_types": ["sco-query"],
            "force_refresh": mode == "new",
            "refresh_latest_month": False,
            "sync_mode": mode,
        }
        pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
        pipeline.clock = lambda: now

        def checkpoint(_repository, **kwargs):
            key = (kwargs["direction"], kwargs["from_date"][:7])
            return {"checkpoint_status": "finalized", "expected_total": 1} if key in synced else None

        with tempfile.TemporaryDirectory() as directory, patch.object(
            InvoiceOverviewRepository, "get_overview_checkpoint", new=checkpoint
        ), patch(
            "app.services.invoice_overview_storage_service.InvoiceOverviewStorageService.verify_finalized_overview_range",
            return_value=True,
        ):
            plan = CoveragePlanner(Path(directory)).plan(
                company_tax_code="0100000000",
                date_from=date.fromisoformat(date_from),
                date_to=date.fromisoformat(date_to),
                directions=list(directions),
                query_types=["sco-query"],
                business_now=now,
                force_refresh=mode == "new",
                force_slices=pipeline._latest_month_force_slices(parameters),
            )
        return {
            (item.direction, item.from_date.strftime("%Y-%m")): item.classification
            for item in plan.decisions
        }

    def test_new_force_refreshes_every_requested_month_even_when_finalized(self):
        synced = {("purchase", f"2026-{month:02d}") for month in range(1, 9)}
        actions = self._coverage_actions(mode="new", synced=synced)
        self.assertEqual(len(actions), 8)
        self.assertTrue(all(value == "realtime_refresh" for value in actions.values()))

    def test_new_force_refreshes_one_finalized_historical_month(self):
        actions = self._coverage_actions(
            mode="new",
            synced={("purchase", "2025-05")},
            date_from="2025-05-01",
            date_to="2025-05-31",
        )
        self.assertEqual(actions[("purchase", "2025-05")], "realtime_refresh")

    def test_supplement_skips_finalized_history_but_rechecks_previous_and_current_month(self):
        synced = {("purchase", f"2026-{month:02d}") for month in range(1, 9)}
        actions = self._coverage_actions(mode="supplement", synced=synced)
        refreshed = {month for (_direction, month), action in actions.items() if action != "stable_finalized_skip"}
        self.assertEqual(refreshed, {"2026-07", "2026-08"})

    def test_supplement_adds_missing_history_to_realtime_window(self):
        synced = {
            ("purchase", f"2026-{month:02d}")
            for month in (1, 2, 4, 5, 6, 7, 8)
        }
        actions = self._coverage_actions(mode="supplement", synced=synced)
        refreshed = {month for (_direction, month), action in actions.items() if action != "stable_finalized_skip"}
        self.assertEqual(refreshed, {"2026-03", "2026-07", "2026-08"})

    def test_supplement_coverage_is_direction_specific(self):
        actions = self._coverage_actions(
            mode="supplement",
            synced={("purchase", "2025-05")},
            directions=("purchase", "sold"),
            date_from="2025-05-01",
            date_to="2025-05-31",
        )
        self.assertEqual(actions[("purchase", "2025-05")], "stable_finalized_skip")
        self.assertEqual(actions[("sold", "2025-05")], "missing")

    def test_supplement_realtime_window_rolls_over_the_year_boundary(self):
        actions = self._coverage_actions(
            mode="supplement",
            synced={("purchase", "2026-12"), ("purchase", "2027-01")},
            date_from="2026-12-01",
            date_to="2027-01-31",
            now=self._business_now(2027, 1, 10),
        )
        self.assertEqual(set(actions.values()), {"realtime_refresh"})

    def test_source_detail_plan_is_materialized_once_and_reused_by_month(self):
        decisions = [
            SimpleNamespace(
                item={"nlap_date": "2025-05-03", "id": "may-1"},
                action="fetch",
                force_refresh=False,
            ),
            SimpleNamespace(
                item={"nlap_date": "2025-05-28", "id": "may-2"},
                action="skip_verified",
                force_refresh=False,
            ),
            SimpleNamespace(
                item={"nlap_date": "2025-06-09", "id": "jun-1"},
                action="refresh",
                force_refresh=True,
            ),
        ]
        planner = _Planner(decisions)
        pipeline = self._pipeline(planner)
        coverage = SimpleNamespace(
            cutoff_date=date(2025, 7, 1),
            decisions=(SimpleNamespace(needs_refresh=False),),
        )
        job = SimpleNamespace(company_tax_code="0100000000")
        parameters = self._parameters()
        original_lookup = InvoiceDetailRepository.get_detail_by_invoice_key

        # Stable overview: the pre-auth source scan is necessary, but it should
        # become the single cached plan used by all later month/stage passes.
        self.assertEqual(
            len(list(pipeline._iter_detail_plan(job, parameters, coverage))),
            3,
        )
        self.assertIs(
            InvoiceDetailRepository.get_detail_by_invoice_key,
            original_lookup,
        )
        pipeline._desktop_overview_complete = True

        may = {"key": "2025-05", "from_date": "2025-05-01", "to_date": "2025-05-31"}
        june = {"key": "2025-06", "from_date": "2025-06-01", "to_date": "2025-06-30"}
        self.assertEqual(
            [item.item["id"] for item in pipeline._month_detail_plan(job, parameters, coverage, may)],
            ["may-1", "may-2"],
        )
        self.assertEqual(
            [item.item["id"] for item in pipeline._month_detail_plan(job, parameters, coverage, may)],
            ["may-1", "may-2"],
        )
        self.assertEqual(
            [item.item["id"] for item in pipeline._month_detail_plan(job, parameters, coverage, june)],
            ["jun-1"],
        )
        self.assertEqual(planner.calls, 1)

    def test_overview_skips_only_post_commit_verification_and_restores_verifier(self):
        calls = []

        def real_verifier(**kwargs):
            calls.append(kwargs)
            return False

        storage = SimpleNamespace(verify_finalized_overview_range=real_verifier)
        planner = SimpleNamespace(storage=storage)
        pipeline = self._pipeline(planner)
        coverage = SimpleNamespace(decisions=(SimpleNamespace(needs_refresh=True),))

        def source_overview(instance, _job, _parameters, _coverage):
            # This simulates the source's defensive reread after its normal
            # run_overview_unit/page/checkpoint commit has succeeded.
            self.assertTrue(
                instance.planner.storage.verify_finalized_overview_range(
                    company_tax_code="0100000000"
                )
            )
            return 4

        with patch.object(InvoiceCrawlPipeline, "_run_overview", new=source_overview):
            self.assertEqual(pipeline._run_overview(object(), {}, coverage), 4)

        self.assertTrue(pipeline._desktop_overview_complete)
        self.assertEqual(calls, [])
        self.assertFalse(storage.verify_finalized_overview_range(test="restored"))
        self.assertEqual(calls, [{"test": "restored"}])

    def test_backend_injects_optimized_source_pipeline(self):
        import mia_backend

        self.assertIs(
            mia_backend.source_backend_module.InvoiceCrawlPipeline,
            OptimizedInvoiceCrawlPipeline,
        )

    def test_xml_wrapper_reuses_source_handler_for_xml_and_html(self):
        observed = []
        pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
        pipeline.core = SimpleNamespace(
            run_xml_unit=lambda _job, payload: (
                observed.append(dict(payload)) or {"outcome": "reused_verified"}
            )
        )
        pipeline._state = {}
        pipeline._desktop_current_unit = None
        pipeline._persist = lambda **_kwargs: None

        pipeline._install_unit_progress_wrappers()
        pipeline.core.run_xml_unit(object(), {
            "direction": "purchase", "query_type": "query", "nbmst": "0101",
            "khhdon": "AA/26E", "shdon": "12", "khmshdon": "1",
            "export_xml": True, "export_html": False,
        })

        self.assertTrue(observed[0]["export_xml"])
        self.assertTrue(observed[0]["export_html"])
        self.assertEqual(pipeline._state["current_artifact"]["shdon"], "12")
        first_key = "purchase|query|0101|AA/26E|12|1"
        self.assertEqual(
            pipeline._state["artifact_progress"]["items"][first_key],
            {"xml": "completed", "html": "completed"},
        )
        self.assertEqual(pipeline._state["artifact_progress"]["completed_xml"], 1)
        self.assertEqual(pipeline._state["artifact_progress"]["completed_html"], 1)

        pipeline.core.run_xml_unit(object(), {
            "direction": "purchase", "query_type": "query", "nbmst": "0101",
            "khhdon": "AA/26E", "shdon": "13", "khmshdon": "1",
        })
        self.assertEqual(pipeline._state["current_artifact"]["shdon"], "13")
        self.assertEqual(pipeline._state["artifact_progress"]["processed"], 2)

    def test_xml_wrapper_records_unavailable_as_failed_without_success_increment(self):
        pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
        pipeline.core = SimpleNamespace(
            run_xml_unit=lambda _job, _payload: {"outcome": "unavailable"}
        )
        pipeline._state = {}
        pipeline._desktop_current_unit = None
        pipeline._persist = lambda **_kwargs: None
        pipeline._install_unit_progress_wrappers()

        pipeline.core.run_xml_unit(object(), {
            "direction": "sold", "query_type": "query", "nbmst": "0101",
            "khhdon": "BB/26E", "shdon": "7", "khmshdon": "1",
        })

        progress = pipeline._state["artifact_progress"]
        self.assertEqual(
            progress["items"]["sold|query|0101|BB/26E|7|1"],
            {"xml": "failed", "html": "failed"},
        )
        self.assertEqual(progress.get("completed_xml", 0), 0)
        self.assertEqual(progress.get("completed_html", 0), 0)


if __name__ == "__main__":
    unittest.main()
