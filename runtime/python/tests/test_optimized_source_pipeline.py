import unittest
from contextlib import closing
from datetime import date
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mia_optimized_source_pipeline import OptimizedInvoiceCrawlPipeline
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_package_repository import InvoicePackageRepository
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

    def test_new_preflight_deletes_only_selected_direction_and_range(self):
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            tax_code = "0100000000"
            database = data_root / tax_code / "db" / "invoices.sqlite3"
            overview = InvoiceOverviewRepository(database)
            InvoiceDetailRepository(database).init_db()
            InvoicePackageRepository(database).init_db()
            common = dict(company_tax_code=tax_code, query_type="sco-query", invoice_category="invoice", raw_json_path="", timestamp="2026-08-01T00:00:00+00:00")
            def item(number, invoice_date):
                return {"nbmst": f"buyer-{number}", "khhdon": "AA", "shdon": str(number), "khmshdon": "1", "nlap": invoice_date}
            overview.upsert_items(direction="purchase", items=[item(1, "2025-01-10"), item(2, "2025-04-01")], **common)
            overview.upsert_items(direction="sold", items=[item(3, "2025-02-10")], **common)

            job = SimpleNamespace(job_id="job-new", company_tax_code=tax_code, parameters={
                "sync_mode": "new", "directions": ["purchase"], "date_from": "2025-01-01", "date_to": "2025-03-31",
            })
            class MetadataRepository:
                def merge_job_parameters(self, _job_id, values):
                    job.parameters = {**job.parameters, **values}
                    return job
            pipeline = object.__new__(OptimizedInvoiceCrawlPipeline)
            pipeline.planner = SimpleNamespace(data_root=data_root)
            pipeline.repository = MetadataRepository()
            prepared = pipeline._prepare_full_replacement(job)
            self.assertEqual(prepared.parameters["replaced_old_count"], 1)
            self.assertTrue(prepared.parameters["replacement_prepared"])
            with closing(sqlite3.connect(database)) as connection:
                remaining = connection.execute("SELECT direction, shdon FROM invoice_overview_items ORDER BY direction, shdon").fetchall()
            self.assertEqual(remaining, [("purchase", "2"), ("sold", "3")])

    def test_replacement_page_is_published_only_after_durable_commit(self):
        repository = Mock()
        original = Mock(return_value=7)
        values = {"company_tax_code": "0100000000", "direction": "purchase", "query_type": "sco-query", "invoice_category": "invoice", "raw_json_path": "", "items": [{"shdon": "1"}], "timestamp": "2026-08-01T00:00:00+00:00"}
        self.assertEqual(OptimizedInvoiceCrawlPipeline._publish_replacement_page(original, repository, **values), 7)
        repository.upsert_items.assert_called_once_with(**values)
        repository.reset_mock()
        with self.assertRaises(RuntimeError):
            OptimizedInvoiceCrawlPipeline._publish_replacement_page(Mock(side_effect=RuntimeError("stage failed")), repository, **values)
        repository.upsert_items.assert_not_called()

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
