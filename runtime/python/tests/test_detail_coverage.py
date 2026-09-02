import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.services.invoice_detail_storage_service import InvoiceDetailStorageService
from app.config.runtime import RuntimeCapabilities


class DetailCoverageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "invoices.sqlite3"
        self.tax_code = "0100000000"
        self.overview = InvoiceOverviewRepository(self.database)
        self.detail = InvoiceDetailRepository(self.database)
        self.overview.init_db()
        self.detail.init_db()

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def item(number: int):
        return {"nbmst": f"020000000{number}", "khhdon": "AA/26E", "shdon": str(number), "khmshdon": "1", "nlap": "2026-01-10"}

    def seed_overview(self, count: int):
        self.overview.upsert_items(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", invoice_category="cash_register",
            raw_json_path="", items=[self.item(index) for index in range(1, count + 1)],
            timestamp="2026-01-31T00:00:00+00:00",
        )

    def save_detail(self, number: int, lines=1):
        item = self.item(number)
        self.detail.replace_normalized_detail_success(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", invoice_category="cash_register",
            **{key: item[key] for key in ("nbmst", "khhdon", "shdon", "khmshdon")},
            nlap=item["nlap"], nlap_date="2026-01-10", raw_detail_path="",
            http_status=200, fetched_at="2026-01-31T00:00:01+00:00",
            lines=[{"ten": f"Dòng {index}"} for index in range(1, lines + 1)],
        )

    def begin(self):
        self.detail.begin_detail_checkpoint(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", from_date="2026-01-01", to_date="2026-01-31",
            overview_expected=2, job_id="job-detail",
            timestamp="2026-01-31T00:00:00+00:00",
        )

    def test_partial_coverage_never_finalizes(self):
        self.seed_overview(2)
        self.save_detail(1)
        self.begin()
        outcome = self.detail.finish_detail_checkpoint(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", from_date="2026-01-01", to_date="2026-01-31",
            job_id="job-detail", timestamp="2026-01-31T00:00:02+00:00",
        )
        self.assertEqual(outcome, {"status": "incomplete", "overview_expected": 2, "detail_succeeded": 1, "detail_failed": 1})

    def test_zero_invoice_range_can_finalize(self):
        self.begin()
        outcome = self.detail.finish_detail_checkpoint(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", from_date="2026-01-01", to_date="2026-01-31",
            job_id="job-detail", timestamp="2026-01-31T00:00:02+00:00",
        )
        self.assertEqual(outcome["status"], "finalized")
        self.assertEqual(outcome["overview_expected"], 0)

    def test_repeated_detail_save_replaces_lines_without_duplicates(self):
        self.seed_overview(1)
        self.save_detail(1, lines=3)
        self.save_detail(1, lines=2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoice_detail_items").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoice_detail_lines").fetchone()[0], 2)

    def test_legacy_zero_line_header_is_not_proof_of_completion(self):
        self.seed_overview(1)
        self.save_detail(1, lines=0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE invoice_detail_items SET detail_outcome='unknown'")
            connection.commit()
        self.begin()
        outcome = self.detail.finish_detail_checkpoint(
            company_tax_code=self.tax_code, direction="purchase", query_type="sco-query",
            from_date="2026-01-01", to_date="2026-01-31", job_id="job-detail",
            timestamp="2026-01-31T00:00:02+00:00",
        )
        self.assertEqual(outcome["status"], "incomplete")
        self.assertEqual(outcome["detail_failed"], 1)

    def test_explicit_valid_empty_detail_is_completed_without_lines(self):
        self.seed_overview(1)
        self.save_detail(1, lines=0)
        self.begin()
        outcome = self.detail.finish_detail_checkpoint(
            company_tax_code=self.tax_code, direction="purchase", query_type="sco-query",
            from_date="2026-01-01", to_date="2026-01-31", job_id="job-detail",
            timestamp="2026-01-31T00:00:02+00:00",
        )
        self.assertEqual(outcome["status"], "finalized")
        self.assertEqual(outcome["detail_succeeded"], 1)

    def test_raw_retention_also_persists_normalized_lines_and_outcome(self):
        self.seed_overview(1)
        item = self.item(1)
        service = InvoiceDetailStorageService(
            self.directory.name, self.detail,
            capabilities=RuntimeCapabilities.from_environment({
                'MIA_RUNTIME_MODE': 'local',
                'MIA_DATA_RETENTION_MODE': 'raw_artifacts',
                'MIA_ENABLE_EXCEL_EXPORT': 'true',
                'MIA_RETAIN_OVERVIEW_RECORDS': 'true',
            }),
        )
        raw_path = service.save_invoice_detail(
            company_tax_code=self.tax_code, direction='purchase',
            query_type='sco-query', invoice_category='cash_register',
            overview_item=item,
            detail={'data_ct': {'hdhhdvu': [{'ten': 'Dòng 1', 'tsuat': 8,
                                             'thtien': '100', 'tthue': '8'}]}},
            from_date='2026-01-01', to_date='2026-01-31',
        )
        self.assertTrue(Path(raw_path).is_file())
        saved = self.detail.get_detail_by_invoice_key(
            self.tax_code, 'purchase', 'sco-query', item['nbmst'], item['khhdon'],
            item['shdon'], item['khmshdon'],
        )
        self.assertEqual(saved['detail_outcome'], 'with_lines')
        self.assertEqual(saved['normalized_line_count'], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM invoice_detail_lines').fetchone()[0], 1)

    def test_staged_overview_refresh_removes_orphan_detail_atomically(self):
        self.seed_overview(2)
        self.save_detail(1)
        self.save_detail(2)
        self.overview.commit_overview_refresh_page(
            refresh_run_id="refresh-1", company_tax_code=self.tax_code,
            direction="purchase", query_type="sco-query",
            invoice_category="cash_register", from_date="2026-01-01",
            to_date="2026-01-31", status_filter="all", page_number=1,
            page_size=50, fetched_count=1, expected_total=1, next_state=None,
            checkpoint_status="completed", items=[self.item(1)],
            timestamp="2026-02-01T00:00:00+00:00", raw_json_path="",
        )
        self.overview.activate_overview_refresh(
            refresh_run_id="refresh-1", company_tax_code=self.tax_code,
            direction="purchase", query_type="sco-query",
            from_date="2026-01-01", to_date="2026-01-31",
            status_filters=["all"], timestamp="2026-02-01T00:00:01+00:00",
        )
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoice_overview_items").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoice_detail_items").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM invoice_detail_lines").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
