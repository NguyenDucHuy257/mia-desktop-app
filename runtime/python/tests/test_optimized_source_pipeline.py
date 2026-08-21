import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from mia_optimized_source_pipeline import OptimizedInvoiceCrawlPipeline
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
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


if __name__ == "__main__":
    unittest.main()
