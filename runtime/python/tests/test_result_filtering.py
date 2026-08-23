import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.job_engine.models import JobRecord
from mia_backend import ProductionBackend, _progress_totals_from_state
from mia_source_results import (
    _EXCLUSION_CACHE,
    _RESULT_ANALYSIS_CACHE,
    _RESULT_COUNT_CACHE,
)


class FakeResultReader:
    rows = []
    detail_rows = []

    def __init__(self, _path):
        pass

    @classmethod
    def _page(cls, rows, limit, cursor):
        start = int(cursor or 0)
        page = rows[start:start + limit]
        end = start + len(page)
        return {
            "items": page,
            "total_count": len(rows),
            "pagination": {
                "limit": limit,
                "has_more": end < len(rows),
                "next_cursor": str(end) if end < len(rows) else None,
            },
        }

    def overview_page(self, _job, *, limit, cursor):
        return self._page(self.rows, limit, cursor)

    def detail_page(self, _job, *, limit, cursor):
        return self._page(self.detail_rows, limit, cursor)


class ResultFilteringTests(unittest.TestCase):
    def setUp(self):
        _RESULT_COUNT_CACHE.clear()
        _RESULT_ANALYSIS_CACHE.clear()
        _EXCLUSION_CACHE.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.backend = object.__new__(ProductionBackend)
        self.backend.data_root = root / "source-data"
        job = JobRecord(
            job_id="job-results",
            account_key="conn_results",
            owner_id="mia-desktop-local",
            company_tax_code="0100000000",
            job_type="invoice_crawl", queue_order=1,
            created_at="2026-01-01T00:00:00+00:00",
            parameters={
                "connection_id": "conn_results",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "directions": ["purchase"],
                "query_types": ["query"],
            },
            status="completed", current_stage=None, worker_id=None, lease_token=None,
            lease_generation=0, lease_expires_at=None,
            available_at="2026-01-01T00:00:00+00:00", cancel_requested_at=None,
            warning_count=0, updated_at="2026-01-01T00:00:00+00:00",
            started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:00+00:00",
            last_error_code=None, last_error_message=None, pipeline_version=3,
        )
        self.backend.repository = SimpleNamespace(
            latest_invoice_job_for_account=lambda *_args, **_kwargs: job,
            list_jobs_for_reconciliation=lambda: [job],
        )
        FakeResultReader.rows = [self.overview_row(index) for index in range(60)]
        FakeResultReader.detail_rows = [self.detail_row(index, line) for index in range(3) for line in range(2)]
        self.reader_patch = patch("app.external_api.results.JobResultReader", FakeResultReader)
        self.reader_patch.start()
        self.addCleanup(self.reader_patch.stop)

    @staticmethod
    def overview_row(index):
        return {
            "id": index + 1,
            "query_type": "query",
            "khmshdon": "1", "khhdon": "AA/26E", "shdon": str(index + 1),
            "nbmst": "0101", "nbten": "Dịch vụ Alpha" if index % 2 == 0 else "Hàng hóa Beta",
            "nmmst": "0202", "nmten": f"Khách {index}", "secret_payload_cell": f"needle-{index}",
            "tgtcthue": 100 + index, "tgtthue": 10, "tgtttbso": 110 + index,
            "dvtte": "VND", "tgia": 1,
        }

    @staticmethod
    def detail_row(index, line):
        return {
            "id": index * 10 + line,
            "query_type": "query",
            "khmshdon": "1", "khhdon": "AA/26E", "shdon": str(index + 1),
            "nbmst": "0101", "ten": "Dịch vụ" if line == 0 else "Phụ phí",
            "dgia": 100, "thtien": 100, "tthue": 10, "tsuat": 10,
        }

    def query(self, **values):
        return {
            "connection_id": "conn_results", "date_from": "2026-01-01",
            "date_to": "2026-01-31", "direction": "purchase",
            "query_type": "query", "limit": 50, **values,
        }

    def test_aggregate_and_global_search_cover_all_source_pages(self):
        result = self.backend.results("overview", self.query())
        self.assertEqual(len(result["items"]), 50)
        self.assertTrue(result["pagination"]["has_more"])
        self.assertEqual(result["aggregate"]["row_count"], 60)
        self.assertEqual(result["aggregate"]["invoice_count"], 60)
        self.assertEqual(result["aggregate"]["totals"]["tgtthue"], 600)

        searched = self.backend.results("overview", self.query(search="needle-57"))
        self.assertEqual(searched["aggregate"]["row_count"], 1)
        self.assertEqual(searched["items"][0]["fields"]["shdon"], "58")

    def test_column_filter_and_unique_values_are_backend_scoped(self):
        result = self.backend.results("overview", self.query(
            column_filters={"nbten": {"values": ["Dịch vụ Alpha"]}}
        ))
        self.assertEqual(result["aggregate"]["row_count"], 30)
        facets = self.backend.result_facets({
            **self.query(), "kind": "overview", "column": "nbten", "facet_limit": 10,
        })
        self.assertEqual(facets["values"], ["Dịch vụ Alpha", "Hàng hóa Beta"])

    def test_exclusion_rule_is_shared_by_invoice_with_detail_lines(self):
        overview_rule = {
            "kind": "overview",
            "query": self.query(column_filters={"shdon": {"values": ["1"]}}),
            "except_keys": [],
        }
        exclusion = {"keys": [], "rules": [overview_rule]}
        overview = self.backend.results("overview", self.query(exclusion=exclusion))
        self.assertEqual(overview["aggregate"]["invoice_count"], 59)
        details = self.backend.results("details", self.query(exclusion=exclusion))
        self.assertEqual(details["aggregate"]["invoice_count"], 2)
        self.assertEqual(details["aggregate"]["row_count"], 4)
        self.assertEqual(details["aggregate"]["totals"]["tthue"], 40)
        excluded_lines = [item for item in details["items"] if item["fields"]["shdon"] == "1"]
        self.assertEqual(len(excluded_lines), 2)
        self.assertTrue(all(item["excluded"] for item in excluded_lines))

    def test_decimal_aggregate_rounds_once_without_binary_float_tail(self):
        FakeResultReader.rows[0]["tgtcthue"] = 1924545.7000000002
        FakeResultReader.rows[1]["tgtcthue"] = 1870182.2999999998
        result = self.backend.results("overview", self.query(
            column_filters={"shdon": {"values": ["1", "2"]}}
        ))
        self.assertEqual(result["aggregate"]["totals"]["tgtcthue"], 3794728)
        self.assertIsInstance(result["aggregate"]["totals"]["tgtcthue"], int)

    def test_explicit_sort_uses_all_matching_rows_and_keeps_cursor_pages(self):
        first = self.backend.results("overview", self.query(
            limit=10, sort={"column": "shdon", "direction": "desc"}
        ))
        self.assertEqual(first["items"][0]["fields"]["shdon"], "9")
        self.assertTrue(first["pagination"]["has_more"])
        second = self.backend.results("overview", self.query(
            limit=10, cursor=first["pagination"]["next_cursor"],
            sort={"column": "shdon", "direction": "desc"},
        ))
        self.assertEqual(len(second["items"]), 10)
        self.assertNotEqual(first["items"][0]["row_id"], second["items"][0]["row_id"])


class CumulativeProgressTests(unittest.TestCase):
    def test_denominator_grows_and_numerator_does_not_reset_between_months(self):
        state = {"modules": {"overview": {"status": "running", "months": [
            {"planned": 100, "processed": 100},
            {"planned": 80, "processed": 5},
            {"planned": None, "processed": 0},
        ]}}}
        self.assertEqual(
            _progress_totals_from_state(state)["overview"],
            {"processed": 105, "total": 180},
        )
        state["modules"]["overview"]["months"][2] = {"planned": 120, "processed": 10}
        self.assertEqual(
            _progress_totals_from_state(state)["overview"],
            {"processed": 115, "total": 300},
        )

    def test_completed_scope_forces_exact_total(self):
        state = {"modules": {"overview": {"status": "completed", "months": [
            {"planned": 250, "processed": 249}, {"planned": 350, "processed": 350},
        ]}}}
        self.assertEqual(
            _progress_totals_from_state(state)["overview"],
            {"processed": 600, "total": 600},
        )


if __name__ == "__main__":
    unittest.main()
