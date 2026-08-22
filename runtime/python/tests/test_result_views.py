import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import mia_runtime
from mia_backend import OWNER_ID, ProductionBackend


class FakeOverviewReader:
    def __init__(self, items):
        self.items = list(items)

    def overview_page(self, _job, *, limit, cursor):
        start = int(cursor or 0)
        visible = self.items[start:start + limit]
        end = start + len(visible)
        has_more = end < len(self.items)
        return {
            "items": visible,
            "pagination": {
                "limit": limit,
                "has_more": has_more,
                "next_cursor": str(end) if has_more else None,
            },
        }


class ResultViewTests(unittest.TestCase):
    def backend_with_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path("source-data")
        job = SimpleNamespace(
            job_id="job-latest",
            owner_id=OWNER_ID,
            account_key="account-1",
            created_at="2026-08-20T10:00:00+00:00",
            updated_at="2026-08-20T10:00:00+00:00",
            company_tax_code="0100000000",
            parameters={
                "connection_id": "account-1",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "directions": ["purchase"],
                "query_types": ["query"],
            },
        )
        backend.repository = SimpleNamespace(list_jobs_for_reconciliation=lambda: [job])
        return backend, job

    @staticmethod
    def replace_fixture_job(job, *, parameters):
        return SimpleNamespace(**{**vars(job), "parameters": parameters})

    def test_invoice_progress_sums_all_months_in_current_module(self):
        job = SimpleNamespace(
            current_stage="detail",
            progress_state={
                "current_stage": "detail",
                "modules": {
                    "overview": {"status": "completed", "months": [{"processed": 20, "planned": 20}]},
                    "detail": {
                        "status": "running",
                        "months": [
                            {"processed": 12, "planned": 20},
                            {"processed": 8, "planned": 30},
                        ],
                    },
                },
            },
        )
        self.assertEqual(
            ProductionBackend._invoice_progress(job),
            {"processed": 20, "planned": 50},
        )

    def test_result_filters_totals_columns_and_exclusions_cover_all_pages(self):
        backend, _ = self.backend_with_job()
        source_items = [
            {
                "id": 1, "direction": "purchase", "nbmst": "0300000000",
                "khhdon": "AA/26E", "shdon": "1", "khmshdon": "1",
                "attributes": {"nmten": "Alpha", "tgtcthue": 1000, "tgtthue": 100},
            },
            {
                "id": 2, "direction": "purchase", "nbmst": "0300000000",
                "khhdon": "AA/26E", "shdon": "2", "khmshdon": "1",
                "attributes": {"nmten": "Beta", "tgtcthue": 2000, "tgtthue": 200},
            },
            {
                "id": 3, "direction": "sold", "nbmst": "0400000000",
                "khhdon": "BB/26E", "shdon": "3", "khmshdon": "1",
                "attributes": {"nmten": "Alpha Service", "tgtcthue": 500, "tgtthue": 50},
            },
        ]
        reader = FakeOverviewReader(source_items)

        with patch("mia_backend.JobResultReader", return_value=reader), patch(
            "mia_backend.replace", side_effect=self.replace_fixture_job,
        ):
            result = backend.results("overview", {
                "connection_id": "account-1",
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "direction": None,
                "limit": 1,
                "column_filters": {"nmten": "alpha"},
                "include_meta": True,
            })

        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["pagination"]["has_more"])
        self.assertEqual(result["meta"]["total_rows"], 2)
        self.assertEqual(result["meta"]["total_invoices"], 2)
        self.assertIn("nmten", result["meta"]["columns"])
        self.assertEqual(result["meta"]["totals"]["tgtcthue"], 1500)
        self.assertEqual(result["meta"]["totals"]["tgtthue"], 150)
        self.assertNotIn("attributes", result["items"][0]["payload"])
        self.assertEqual(result["items"][0]["payload"]["nmten"], "Alpha")

        excluded = result["items"][0]["business_key"]
        with patch(
            "mia_backend.JobResultReader",
            return_value=FakeOverviewReader(source_items),
        ), patch("mia_backend.replace", side_effect=self.replace_fixture_job):
            filtered = backend.results("overview", {
                "connection_id": "account-1",
                "limit": 50,
                "column_filters": {"nmten": "alpha"},
                "exclude_business_keys": [excluded],
                "include_meta": True,
            })
        self.assertEqual(filtered["meta"]["total_rows"], 1)
        self.assertEqual(filtered["meta"]["totals"]["tgtcthue"], 500)
        self.assertTrue(all(item["business_key"] != excluded for item in filtered["items"]))

    def test_result_dispatch_initializes_production_backend_after_restart(self):
        previous = (
            mia_runtime.storage,
            mia_runtime.data_directory,
            mia_runtime.logger,
            mia_runtime.production_backend,
        )
        backend = Mock()
        backend.results.return_value = {
            "items": [{"overview_id": 1}],
            "pagination": {"limit": 50, "has_more": False, "next_cursor": None},
        }
        try:
            with tempfile.TemporaryDirectory() as directory, patch(
                "mia_runtime.ProductionBackend", return_value=backend,
            ) as constructor:
                mia_runtime.storage = object()
                mia_runtime.data_directory = Path(directory)
                mia_runtime.logger = None
                mia_runtime.production_backend = None
                query = {
                    "connection_id": "account-1",
                    "date_from": "2026-02-01",
                    "date_to": "2026-02-28",
                }
                result, should_stop = mia_runtime.dispatch("results.overview", query)
                self.assertFalse(should_stop)
                self.assertEqual(result["items"][0]["overview_id"], 1)
                constructor.assert_called_once_with(Path(directory), None)
                backend.results.assert_called_once_with("overview", query)
        finally:
            (
                mia_runtime.storage,
                mia_runtime.data_directory,
                mia_runtime.logger,
                mia_runtime.production_backend,
            ) = previous

    def test_result_workbook_applies_filters_only_to_current_scope_and_excludes_invoices(self):
        backend, _ = self.backend_with_job()
        backend._all_result_rows = Mock(side_effect=[
            [{"direction": "purchase", "business_key": "A", "payload": {"shdon": "1"}}],
            [{"direction": "purchase", "business_key": "A", "line_key": "1", "payload": {"ten": "Dịch vụ"}}],
        ])
        with tempfile.TemporaryDirectory() as directory:
            result = backend.export_results({
                "destination": directory,
                "connection_ids": ["account-1"],
                "result_scopes": ["overview", "details"],
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "direction": None,
                "search": "",
                "column_filters": {"ten": "dịch vụ"},
                "filter_scope": "details",
                "exclude_business_keys": ["DROP-ME"],
            })
            target = Path(result["files"][0])
            self.assertTrue(target.is_file())
            workbook = load_workbook(target, read_only=True)
            self.assertEqual(workbook.sheetnames, ["Tong quan", "Chi tiet"])
            workbook.close()

        overview_query = backend._all_result_rows.call_args_list[0].args[1]
        detail_query = backend._all_result_rows.call_args_list[1].args[1]
        self.assertEqual(overview_query["column_filters"], {})
        self.assertEqual(detail_query["column_filters"], {"ten": "dịch vụ"})
        self.assertEqual(detail_query["exclude_business_keys"], ["DROP-ME"])


if __name__ == "__main__":
    unittest.main()
