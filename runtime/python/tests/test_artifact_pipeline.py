import json
import sqlite3
import sys
import tempfile
import unittest
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "mia_crawl_service"
sys.path.insert(0, str(VENDOR_ROOT))
from app.services.overview_downloader import ELECTRONIC_STATUSES
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from mia_artifact_pipeline import (
    ArtifactBatchCoordinator,
    ArtifactInspector,
    _PdfWorkerPool,
    _create_pdf_renderer,
    _copy_atomically,
    _missing_intervals,
    _pdf_cache_paths,
    build_invoice_export_basename,
    html_dependency_files,
    html_fingerprint,
    pdf_cache_valid,
)
from unittest.mock import Mock, patch
from datetime import date


class FakeBackend:
    def __init__(self, root: Path, tax_code: str = "0101234567") -> None:
        self.data_root = root
        self.tax_code = tax_code

    def connection_tax_code(self, _connection_id: str) -> str:
        return self.tax_code


class VatBackend(FakeBackend):
    def __init__(self, root: Path, jobs=()) -> None:
        super().__init__(root)
        self.repository = SimpleNamespace(invoice_jobs_for_account=lambda _connection_id: list(jobs))


class MappingBackend:
    def __init__(self, root: Path, identities: dict[str, str]) -> None:
        self.data_root = root
        self.identities = identities

    def connection_tax_code(self, connection_id: str) -> str:
        return self.identities[connection_id]


class ArtifactPipelineTests(unittest.TestCase):
    def _database(self, root: Path) -> Path:
        database = root / "0101234567" / "db" / "invoices.sqlite3"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("""
                CREATE TABLE invoice_overview_items(
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, nbmst TEXT, khhdon TEXT, shdon TEXT,
                    khmshdon TEXT, nlap_date TEXT
                );
                CREATE TABLE invoice_overview_checkpoints(
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, from_date TEXT, to_date TEXT,
                    status_filter TEXT, checkpoint_status TEXT,
                    fetched_count INTEGER, expected_total INTEGER, page_number INTEGER
                );
            """)
        return database

    def _checkpoint_month(
        self, database: Path, month: int, status: str = "finalized",
        direction: str = "purchase",
    ) -> None:
        begin = date(2026, month, 1)
        end = date(2026, month + 1, 1) if month < 12 else date(2027, 1, 1)
        end = end.fromordinal(end.toordinal() - 1)
        rows = []
        for query_type in ("query", "sco-query"):
            filters = [str(value) for value in ELECTRONIC_STATUSES] if query_type == "query" else ["all"]
            rows.extend((
                "0101234567", direction, query_type, begin.isoformat(),
                end.isoformat(), item, status, 0, 0, 0,
            ) for item in filters)
        with closing(sqlite3.connect(database)) as connection:
            connection.executemany(
                """INSERT INTO invoice_overview_checkpoints(
                    company_tax_code,direction,query_type,from_date,to_date,
                    status_filter,checkpoint_status,fetched_count,expected_total,page_number
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""", rows,
            )
            connection.commit()

    def _snapshot(self, root: Path):
        return ArtifactInspector(FakeBackend(root)).snapshot({
            "connection_ids": ["conn_1"], "directions": ["purchase"],
            "date_from": "2026-01-01", "date_to": "2026-03-31",
        })["accounts"][0]

    def _detail_checkpoint(self, database: Path, month: int, direction="purchase", status="finalized"):
        repository = InvoiceDetailRepository(database)
        begin = date(2026, month, 1)
        following = date(2026, month + 1, 1) if month < 12 else date(2027, 1, 1)
        end = date.fromordinal(following.toordinal() - 1)
        for query_type in ("query", "sco-query"):
            repository.begin_detail_checkpoint(
                company_tax_code="0101234567", direction=direction,
                query_type=query_type, from_date=begin.isoformat(),
                to_date=end.isoformat(), overview_expected=0,
                job_id="job_detail", timestamp="2026-01-01T00:00:00+00:00",
            )
            if status == "finalized":
                repository.finish_detail_checkpoint(
                    company_tax_code="0101234567", direction=direction,
                    query_type=query_type, from_date=begin.isoformat(),
                    to_date=end.isoformat(), job_id="job_detail",
                    timestamp="2026-01-01T00:00:01+00:00",
                )
            else:
                repository.mark_detail_checkpoint_status(
                    company_tax_code="0101234567", job_id="job_detail",
                    status=status, timestamp="2026-01-01T00:00:01+00:00",
                )

    @staticmethod
    def _detail_job(direction="purchase", status="completed", months=(1, 2, 3)):
        month_rows = []
        for month in months:
            begin = date(2026, month, 1)
            following = date(2026, month + 1, 1) if month < 12 else date(2027, 1, 1)
            month_rows.append({"from_date": begin.isoformat(), "to_date": date.fromordinal(following.toordinal() - 1).isoformat(), "status": "completed"})
        return SimpleNamespace(status=status, parameters={"directions": [direction], "result_scope": "detail"}, progress_state={"modules": {"detail": {"status": "completed", "months": month_rows}}})

    def test_vat_coverage_requires_overview_and_detail_for_each_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for direction in ("purchase", "sold"):
                for month in (1, 2, 3):
                    self._checkpoint_month(database, month, direction=direction)
                    self._detail_checkpoint(database, month, direction=direction)
            account = ArtifactInspector(VatBackend(root, [self._detail_job("purchase"), self._detail_job("sold")])).vat_return_coverage({"connection_ids": ["conn_1"], "date_from": "2026-01-01", "date_to": "2026-03-31"})["accounts"][0]
            self.assertTrue(account["purchase"]["ready"])
            self.assertTrue(account["sold"]["ready"])

    def test_vat_coverage_reports_scope_gap_and_rejects_failed_detail_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for month in (1, 3):
                self._checkpoint_month(database, month, direction="purchase")
            self._detail_checkpoint(database, 1, direction="purchase", status="failed")
            account = ArtifactInspector(VatBackend(root, [self._detail_job("purchase", status="failed")])).vat_return_coverage({"connection_ids": ["conn_1"], "date_from": "2026-01-01", "date_to": "2026-03-31"})["accounts"][0]["purchase"]
            self.assertIn({"scope": "overview", "date_from": "2026-02-01", "date_to": "2026-02-28"}, account["missing"])
            self.assertIn({"scope": "details", "date_from": "2026-01-01", "date_to": "2026-03-31"}, account["missing"])

    def test_vat_finalized_zero_invoice_period_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 1, direction="purchase")
            self._detail_checkpoint(database, 1, direction="purchase")
            account = ArtifactInspector(VatBackend(root, [self._detail_job("purchase", months=(1,))])).vat_return_coverage({"connection_ids": ["conn_1"], "date_from": "2026-01-01", "date_to": "2026-01-31"})["accounts"][0]["purchase"]
            self.assertTrue(account["ready"])

    def test_vat_stale_finalized_checkpoint_does_not_hide_missing_invoice_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 1, direction="sold")
            self._detail_checkpoint(database, 1, direction="sold")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """INSERT INTO invoice_overview_items(
                           company_tax_code,direction,query_type,nbmst,khhdon,shdon,
                           khmshdon,nlap_date) VALUES(?,?,?,?,?,?,?,?)""",
                    ("0101234567", "sold", "query", "0200000000", "AA/26E",
                     "1", "1", "2026-01-10"),
                )
                connection.commit()
            account = ArtifactInspector(VatBackend(root)).vat_return_coverage({
                "connection_ids": ["conn_1"], "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            })["accounts"][0]["sold"]
            self.assertFalse(account["detail_ready"])
            self.assertIn({"scope": "details", "date_from": "2026-01-01",
                           "date_to": "2026-01-31"}, account["missing"])

    def test_vat_overview_requires_every_normal_and_cash_register_source_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 1, direction="purchase")
            self._detail_checkpoint(database, 1, direction="purchase")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DELETE FROM invoice_overview_checkpoints WHERE query_type='query' AND status_filter='8'")
                connection.commit()
            account = ArtifactInspector(VatBackend(root, [self._detail_job("purchase", months=(1,))])).vat_return_coverage({"connection_ids": ["conn_1"], "date_from": "2026-01-01", "date_to": "2026-01-31"})["accounts"][0]["purchase"]
            self.assertIn({"scope": "overview", "date_from": "2026-01-01", "date_to": "2026-01-31"}, account["missing"])

    def test_middle_month_gap_is_not_covered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 1)
            self._checkpoint_month(database, 3)
            snapshot = self._snapshot(root)
            self.assertFalse(snapshot["ready"])
            self.assertIn(
                {"date_from": "2026-02-01", "date_to": "2026-02-28"},
                snapshot["missing_ranges"],
            )

    def test_paused_or_incomplete_final_month_is_not_covered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 1)
            self._checkpoint_month(database, 2)
            self._checkpoint_month(database, 3, status="in_progress")
            snapshot = self._snapshot(root)
            self.assertFalse(snapshot["ready"])
            self.assertEqual(snapshot["missing_ranges"][-1]["date_to"], "2026-03-31")

    def test_every_required_checkpoint_makes_the_full_range_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for month in (1, 2, 3):
                self._checkpoint_month(database, month)
            self.assertTrue(self._snapshot(root)["ready"])

    def test_coverage_advances_after_each_persisted_month_finalize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            inspector = ArtifactInspector(FakeBackend(root))
            request = {
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "date_from": "2026-02-01", "date_to": "2026-05-31",
            }

            expected_missing_starts = (
                (2, "2026-03-01"),
                (3, "2026-04-01"),
                (4, "2026-05-01"),
            )
            for month, missing_start in expected_missing_starts:
                self._checkpoint_month(database, month)
                snapshot = inspector.coverage(request)["accounts"][0]
                self.assertFalse(snapshot["ready"])
                self.assertEqual(snapshot["missing_ranges"][0]["date_from"], missing_start)
                self.assertEqual(snapshot["missing_ranges"][-1]["date_to"], "2026-05-31")

            self._checkpoint_month(database, 5)
            snapshot = inspector.coverage(request)["accounts"][0]
            self.assertTrue(snapshot["ready"])
            self.assertEqual(snapshot["missing_ranges"], [])

    def test_coverage_reports_only_tail_after_three_finalized_months(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for month in (1, 2, 3):
                self._checkpoint_month(database, month)
            account = ArtifactInspector(FakeBackend(root)).coverage({
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "date_from": "2026-01-01", "date_to": "2026-08-31",
            })["accounts"][0]
            self.assertEqual(account["missing_ranges"], [{
                "date_from": "2026-04-01", "date_to": "2026-08-31",
            }])

    def test_coverage_preserves_disjoint_month_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for month in (1, 2, 3, 5, 6):
                self._checkpoint_month(database, month)
            account = ArtifactInspector(FakeBackend(root)).coverage({
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "date_from": "2026-01-01", "date_to": "2026-08-31",
            })["accounts"][0]
            self.assertEqual(account["missing_ranges"], [
                {"date_from": "2026-04-01", "date_to": "2026-04-30"},
                {"date_from": "2026-07-01", "date_to": "2026-08-31"},
            ])

    def test_one_finalized_source_scope_is_not_poisoned_by_absent_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """INSERT INTO invoice_overview_checkpoints(
                        company_tax_code,direction,query_type,from_date,to_date,
                        status_filter,checkpoint_status,fetched_count,expected_total,page_number
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    ("0101234567", "purchase", "query", "2026-01-01", "2026-03-31",
                     "5", "finalized", 10, 10, 1),
                )
                connection.commit()
            account = ArtifactInspector(FakeBackend(root)).coverage({
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "date_from": "2026-01-01", "date_to": "2026-08-31",
            })["accounts"][0]
            self.assertEqual(account["missing_ranges"], [{
                "date_from": "2026-04-01", "date_to": "2026-08-31",
            }])

    def test_coverage_is_scoped_to_the_selected_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            for month in (2, 3, 4, 5):
                self._checkpoint_month(database, month, direction="purchase")
            for month in (2, 3):
                self._checkpoint_month(database, month, direction="sold")
            inspector = ArtifactInspector(FakeBackend(root))
            common = {
                "connection_ids": ["conn_1"],
                "date_from": "2026-02-01", "date_to": "2026-05-31",
            }

            purchase = inspector.coverage({**common, "directions": ["purchase"]})["accounts"][0]
            sold = inspector.coverage({**common, "directions": ["sold"]})["accounts"][0]
            self.assertTrue(purchase["ready"])
            self.assertFalse(sold["ready"])
            self.assertEqual(
                sold["missing_ranges"],
                [{"date_from": "2026-04-01", "date_to": "2026-05-31"}],
            )

    def test_lightweight_coverage_never_scans_package_or_pdf_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            self._checkpoint_month(database, 2)
            inspector = ArtifactInspector(FakeBackend(root))
            with patch.object(inspector, "_account_snapshot", side_effect=AssertionError("artifact scan invoked")):
                account = inspector.coverage({
                    "connection_ids": ["conn_1"], "directions": ["purchase"],
                    "date_from": "2026-02-01", "date_to": "2026-02-28",
                })["accounts"][0]
            self.assertTrue(account["ready"])
            self.assertEqual(account["missing_ranges"], [])

    def test_coverage_maps_each_connection_to_its_own_tax_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready_database = self._database(root)
            self._checkpoint_month(ready_database, 2)
            missing_database = root / "0201234567" / "db" / "invoices.sqlite3"
            missing_database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(missing_database)) as connection:
                connection.execute("""CREATE TABLE invoice_overview_checkpoints(
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, from_date TEXT, to_date TEXT,
                    status_filter TEXT, checkpoint_status TEXT,
                    fetched_count INTEGER, expected_total INTEGER, page_number INTEGER
                )""")
            inspector = ArtifactInspector(MappingBackend(root, {
                "conn_ready": "0101234567", "conn_missing": "0201234567",
            }))
            accounts = inspector.coverage({
                "connection_ids": ["conn_ready", "conn_missing"],
                "directions": ["purchase"], "date_from": "2026-02-01", "date_to": "2026-02-28",
            })["accounts"]
            self.assertEqual(accounts[0]["connection_id"], "conn_ready")
            self.assertTrue(accounts[0]["ready"])
            self.assertEqual(accounts[1]["connection_id"], "conn_missing")
            self.assertFalse(accounts[1]["ready"])

    def test_missing_interval_helper_detects_internal_holes(self):
        self.assertEqual(
            _missing_intervals(
                date(2026, 1, 1), date(2026, 3, 31),
                [(date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 3, 1), date(2026, 3, 31))],
            ),
            [(date(2026, 2, 1), date(2026, 2, 28))],
        )

    def test_day_granular_checkpoint_reports_only_the_remaining_fifteen_days(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = self._database(root)
            rows = []
            for query_type in ("query", "sco-query"):
                filters = [str(value) for value in ELECTRONIC_STATUSES] if query_type == "query" else ["all"]
                rows.extend((
                    "0101234567", "purchase", query_type, "2026-08-01", "2026-08-16",
                    item, "finalized", 0, 0, 1,
                ) for item in filters)
            with closing(sqlite3.connect(database)) as connection:
                connection.executemany(
                    """INSERT INTO invoice_overview_checkpoints(
                        company_tax_code,direction,query_type,from_date,to_date,
                        status_filter,checkpoint_status,fetched_count,expected_total,page_number
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""", rows,
                )
                connection.commit()
            snapshot = ArtifactInspector(FakeBackend(root)).coverage({
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "date_from": "2026-08-01", "date_to": "2026-08-31",
            })["accounts"][0]
            self.assertFalse(snapshot["ready"])
            self.assertEqual(snapshot["missing_ranges"], [{
                "date_from": "2026-08-17", "date_to": "2026-08-31",
            }])

    def test_html_is_ready_only_with_local_references_and_required_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = root / "invoice.html"
            html.write_text('<html><link href="assets/style.css"><img src="assets/logo.png"></html>', encoding="utf-8")
            (root / "assets").mkdir()
            (root / "assets" / "style.css").write_text("body{}", encoding="utf-8")
            (root / "assets" / "logo.png").write_bytes(b"image")
            self.assertIsNone(html_dependency_files(html))
            for name in ("sign-check.jpg", "viewinvoice-bg.jpg"):
                (root / name).write_bytes(name.encode())
            self.assertIsNotNone(html_dependency_files(html))

    def test_html_change_invalidates_a_cached_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = root / "invoice.html"
            html.write_text("<html>old</html>", encoding="utf-8")
            for name in ("sign-check.jpg", "viewinvoice-bg.jpg"):
                (root / name).write_bytes(name.encode())
            fingerprint = html_fingerprint(html)
            pdf, metadata = _pdf_cache_paths(root, "0101234567", "purchase|query|a")
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            metadata.write_text(json.dumps({"html_fingerprint": fingerprint}), encoding="utf-8")
            self.assertTrue(pdf_cache_valid(root, "0101234567", "purchase|query|a", html))
            html.write_text("<html>new</html>", encoding="utf-8")
            self.assertFalse(pdf_cache_valid(root, "0101234567", "purchase|query|a", html))

    def _package_cache(self, root: Path, targets: list[dict]) -> None:
        database = root / "0101234567" / "db" / "invoices.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("""CREATE TABLE invoice_package_items(
                id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                query_type TEXT, nbmst TEXT, khhdon TEXT, shdon TEXT,
                khmshdon TEXT, xml_path TEXT, html_path TEXT,
                xml_fetched INTEGER, html_fetched INTEGER, unavailable INTEGER,
                updated_at TEXT
            )""")
            for index, target in enumerate(targets, 1):
                package = root / f"package-{index}"
                package.mkdir()
                xml = package / f"invoice-{index}.xml"
                html = package / f"invoice-{index}.html"
                xml.write_text("<invoice/>", encoding="utf-8")
                html.write_text("<html>ready</html>", encoding="utf-8")
                (package / "sign-check.jpg").write_bytes(b"sign")
                (package / "viewinvoice-bg.jpg").write_bytes(b"background")
                connection.execute(
                    """INSERT INTO invoice_package_items VALUES(
                       ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (index, "0101234567", target["direction"], target["query_type"],
                     target["nbmst"], target["khhdon"], target["shdon"],
                     target["khmshdon"], str(xml), str(html), 1, 1, 0, "now"),
                )
            connection.commit()

    def test_one_shared_package_call_serves_xml_and_html_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {
                "artifact_key": "purchase|query|0101|C23TTL|00109|1",
                "direction": "purchase", "query_type": "query", "nbmst": "0101",
                "khhdon": "C23TTL", "shdon": "00109", "khmshdon": "1",
                "nlap_date": "2023-10-15",
            }
            self._package_cache(root, [target])

            class Backend(FakeBackend):
                def __init__(self, data_root):
                    super().__init__(data_root)
                    self.package_calls = 0

                def artifact_targets_for_export(self, request):
                    return [target] if request["query_type"] == "query" else []

                def ensure_invoice_packages(self, request, **kwargs):
                    self.package_calls += 1
                    kwargs["ready_callback"](target, "completed", "reused_verified")

            backend = Backend(root)
            snapshot = {"accounts": [{
                "connection_id": "conn_1", "ready": True, "missing_ranges": [],
                "total": 1, "cached": {"xml": 1, "html": 1, "pdf": 0},
            }]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                result = ArtifactBatchCoordinator(backend, {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["xml", "html"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 5,
                }).run()
            self.assertEqual(backend.package_calls, 1)
            self.assertEqual(result["status"], "completed")
            xml = next((root / "output").rglob("*.xml"))
            html = next((root / "output").rglob("*.html"))
            self.assertEqual(xml.stem, "20231015_1_C23TTL_00109_0101")
            self.assertEqual(html.stem, xml.stem)
            self.assertTrue((html.parent / "sign-check.jpg").is_file())
            self.assertTrue((html.parent / "viewinvoice-bg.jpg").is_file())

    def test_export_basename_sanitizes_windows_characters_and_missing_fields(self):
        self.assertEqual(build_invoice_export_basename({
            "nlap": "15/10/2023 09:00", "khmshdon": "1/2",
            "khhdon": 'C23:TTL*?', "shdon": "000109", "nbmst": None,
        }), "20231015_1_2_C23_TTL_000109")
        value = build_invoice_export_basename({
            "nlap_date": "2023-10-15", "khmshdon": None,
            "khhdon": " ", "shdon": "109.", "nbmst": "undefined",
        })
        self.assertEqual(value, "20231015_109")
        self.assertNotRegex(value, r'[<>:"/\\|?*]|[. ]$|None|null|undefined')

    def test_atomic_export_overwrites_same_canonical_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.xml"
            output = root / "output"
            source.write_text("first", encoding="utf-8")
            first = _copy_atomically(source, output, "20260101_1_A_1")
            source.write_text("second", encoding="utf-8")
            second = _copy_atomically(source, output, "20260101_1_A_1")
            self.assertEqual(first, second)
            self.assertEqual(second.read_text(encoding="utf-8"), "second")
            self.assertEqual([item.name for item in output.glob("*.xml")], ["20260101_1_A_1.xml"])
            self.assertFalse(list(output.glob("*.tmp")))

    def test_xml_only_does_not_create_an_html_output_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {
                "artifact_key": "purchase|query|0101|AA|1|1",
                "direction": "purchase", "query_type": "query", "nbmst": "0101",
                "khhdon": "AA", "shdon": "1", "khmshdon": "1",
            }
            self._package_cache(root, [target])

            class Backend(FakeBackend):
                def artifact_targets_for_export(self, request):
                    return [target] if request["query_type"] == "query" else []
                def ensure_invoice_packages(self, _request, **kwargs):
                    kwargs["ready_callback"](target, "completed", "reused_verified")

            snapshot = {"accounts": [{"connection_id": "conn_1", "ready": True, "missing_ranges": [], "total": 1, "cached": {"xml": 1, "html": 1, "pdf": 0}}]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                ArtifactBatchCoordinator(Backend(root), {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["xml"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 5,
                }).run()
            self.assertEqual(len(list((root / "output").rglob("*.xml"))), 1)
            self.assertFalse(any(path.name.startswith("HTML ") for path in (root / "output").rglob("*")))

    def test_pdf_worker_starts_before_the_package_batch_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = [{
                "artifact_key": f"purchase|query|0101|AA|{index}|1",
                "direction": "purchase", "query_type": "query", "nbmst": "0101",
                "khhdon": "AA", "shdon": str(index), "khmshdon": "1",
            } for index in (1, 2)]
            self._package_cache(root, targets)
            first_pdf_started = threading.Event()
            package_saw_streaming = []

            class Backend(FakeBackend):
                def artifact_targets_for_export(self, request):
                    return targets if request["query_type"] == "query" else []
                def ensure_invoice_packages(self, _request, **kwargs):
                    kwargs["ready_callback"](targets[0], "completed", "reused_verified")
                    package_saw_streaming.append(first_pdf_started.wait(1))
                    kwargs["ready_callback"](targets[1], "completed", "reused_verified")

            class Coordinator(ArtifactBatchCoordinator):
                def _render_pdf_item(self, item, _tax_code, _output_root, _get_renderer):
                    first_pdf_started.set()
                    self._advance("pdf", item["display"])

            snapshot = {"accounts": [{"connection_id": "conn_1", "ready": True, "missing_ranges": [], "total": 2, "cached": {"xml": 2, "html": 2, "pdf": 0}}]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                Coordinator(Backend(root), {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["pdf"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 1,
                }).run()
            self.assertEqual(package_saw_streaming, [True])

    def test_pdf_worker_queue_applies_bounded_backpressure(self):
        pool = _PdfWorkerPool(3, lambda _item, _renderer: None)
        try:
            self.assertEqual(pool.queue.maxsize, 6)
        finally:
            pool.finish()

    def test_pdf_render_uses_real_package_assets_when_global_assets_are_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            package.mkdir()
            html = package / "invoice.html"
            html.write_text("<html><body>invoice</body></html>", encoding="utf-8")
            (package / "sign-check.jpg").write_bytes(b"real-sign")
            (package / "viewinvoice-bg.jpg").write_bytes(b"real-background")
            target = {
                "artifact_key": "purchase|query|0101|C23TTL|00109|1",
                "nlap_date": "2023-10-15", "khmshdon": "1",
                "khhdon": "C23TTL", "shdon": "00109", "nbmst": "0101",
            }
            coordinator = object.__new__(ArtifactBatchCoordinator)
            coordinator.data_root = root
            coordinator.global_cancel = threading.Event()
            coordinator.format_cancel = {"pdf": threading.Event()}
            coordinator.logger = None
            coordinator._mark_cached = lambda *_args: None
            coordinator._advance = lambda *_args, **_kwargs: None

            class Renderer:
                def render_pdf(self, html_path, pdf_path):
                    self.html_path = html_path
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(b"%PDF-1.4\npackage-assets\n%%EOF")

            renderer = Renderer()
            asset_roots = []
            coordinator._render_pdf_item({
                "display": "AA - 1", "target": target, "html_path": html,
                "connection_id": "conn_1", "cache_keys": {"pdf": set()},
            }, "0101234567", root / "output", lambda assets: asset_roots.append(assets) or renderer)
            exported = next((root / "output").glob("*.pdf"))
            self.assertEqual(exported.stem, "20231015_1_C23TTL_00109_0101")
            self.assertEqual(asset_roots, [package])
            self.assertTrue(exported.read_bytes().startswith(b"%PDF-"))
            self.assertTrue(exported.read_bytes().rstrip().endswith(b"%%EOF"))

            from app.exporters.invoice_pdf_renderer import InvoicePdfRenderer
            pdf_renderer = InvoicePdfRenderer(assets_dir=package)
            self.assertIn("cmVhbC1zaWdu", pdf_renderer.page_asset_css)
            remote_url = "https" + "://example.invalid/remote.png"
            self.assertFalse(pdf_renderer._resource_url_is_allowed(remote_url))

            desktop_renderer = _create_pdf_renderer(package)
            class Page:
                @staticmethod
                def pdf(*, path, **_options):
                    Path(path).write_bytes(b"%PDF-1.4\nreal-render-output\n%%EOF")
            desktop_renderer._page = Page()
            atomic_pdf = root / "atomic.pdf"
            desktop_renderer._render_pdf_atomically(atomic_pdf)
            self.assertTrue(atomic_pdf.read_bytes().startswith(b"%PDF-"))
            self.assertTrue(atomic_pdf.read_bytes().rstrip().endswith(b"%%EOF"))

    def test_missing_original_completes_without_warning_or_failed_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {
                "artifact_key": "purchase|query|0101|AA|1|1",
                "direction": "purchase", "query_type": "query", "nbmst": "0101",
                "khhdon": "AA", "shdon": "1", "khmshdon": "1",
            }
            class Backend(FakeBackend):
                def artifact_targets_for_export(self, request):
                    return [target] if request["query_type"] == "query" else []
                def ensure_invoice_packages(self, _request, **kwargs):
                    kwargs["ready_callback"](target, "missing_original", "missing_original")
            snapshot = {"accounts": [{"connection_id": "conn_1", "ready": True, "missing_ranges": [], "total": 1, "cached": {"xml": 0, "html": 0, "pdf": 0}}]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                result = ArtifactBatchCoordinator(Backend(root), {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["xml", "html"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 5,
                }).run()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["accounts"]["conn_1"]["status"], "completed")
            self.assertEqual(result["formats"]["xml"]["failed"], 0)
            self.assertEqual(result["formats"]["html"]["failed"], 0)
            self.assertEqual(result["formats"]["xml"]["processed"], 0)
            self.assertEqual(result["formats"]["html"]["processed"], 0)
            self.assertEqual(result["formats"]["xml"]["skipped"], 1)
            self.assertEqual(result["warning_count"], 0)
            self.assertEqual(result["accounts"]["conn_1"]["failure_count"], 1)
            coordinator = ArtifactBatchCoordinator(Backend(root), {
                "destination": str(root / "output-2"), "connection_ids": ["conn_1"],
                "directions": ["purchase"], "kinds": ["xml", "html"],
                "date_from": "2026-01-01", "date_to": "2026-01-31",
                "pdf_concurrency": 5,
            })
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                coordinator.run()
            failures, total = coordinator.failure_view("conn_1")
            self.assertEqual(total, 1)
            self.assertEqual(failures[0]["category"], "missing_original")
            self.assertEqual(failures[0]["message"], "Không tồn tại hồ sơ gốc")
            self.assertEqual(failures[0]["affected_formats"], ["html", "xml"])

    def test_retry_exhausted_package_is_one_structured_terminal_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {
                "artifact_key": "purchase|query|0101|AA|1|1",
                "direction": "purchase", "query_type": "query", "nbmst": "0101",
                "khhdon": "AA", "shdon": "1", "khmshdon": "1",
            }
            class Backend(FakeBackend):
                def artifact_targets_for_export(self, request):
                    return [target] if request["query_type"] == "query" else []
                def ensure_invoice_packages(self, _request, **kwargs):
                    kwargs["ready_callback"](target, "unavailable", "source_retry_exhausted")
            snapshot = {"accounts": [{"connection_id": "conn_1", "ready": True, "missing_ranges": [], "total": 1, "cached": {"xml": 0, "html": 0, "pdf": 0}}]}
            logger = Mock()
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                result = ArtifactBatchCoordinator(Backend(root), {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["xml"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 1,
                }, logger=logger).run()
            self.assertEqual(result["formats"]["xml"]["failed"], 1)
            self.assertEqual(result["warning_count"], 1)
            self.assertEqual(result["accounts"]["conn_1"]["failure_count"], 1)
            logger.warning.assert_not_called()

    def test_structured_failures_deduplicate_invoice_and_merge_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = ArtifactBatchCoordinator(FakeBackend(Path(directory)), {
                "destination": str(Path(directory) / "output"),
                "connection_ids": ["conn_1"], "directions": ["purchase"],
                "kinds": ["xml", "html", "pdf"],
                "date_from": "2026-01-01", "date_to": "2026-01-31",
                "pdf_concurrency": 1,
            })
            coordinator.state["accounts"] = {"conn_1": {"failure_count": 0}}
            target = {
                "artifact_key": "purchase|query|0101|AA|1|1",
                "direction": "purchase", "query_type": "query",
                "nbmst": "0101", "khhdon": "AA", "shdon": "1",
                "khmshdon": "1", "nlap_date": "2026-01-15",
                "partner_name": "Đối tác",
            }
            coordinator._record_failure(
                "conn_1", target, ["xml", "html"],
                "source_retry_exhausted", "Không lấy được gói dữ liệu sau 7 lần thử",
            )
            coordinator._record_failure(
                "conn_1", target, ["pdf"], "pdf_failed", "Chromium render failed",
            )
            coordinator._record_failure(
                "conn_1", target, ["pdf"], "pdf_failed", "token=secret-value timeout",
            )
            items, total = coordinator.failure_view("conn_1")
            self.assertEqual(total, 1)
            self.assertEqual(items[0]["affected_formats"], ["html", "pdf", "xml"])
            self.assertIn("Chromium render failed", items[0]["message"])
            self.assertNotIn("secret-value", items[0]["message"])
            self.assertEqual(coordinator.view()["accounts"]["conn_1"]["failure_count"], 1)

    def test_user_cancellation_marks_account_stopped_not_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {"artifact_key": "purchase|query|x", "direction": "purchase", "query_type": "query", "nbmst": "x", "khhdon": "A", "shdon": "1", "khmshdon": "1"}
            class Backend(FakeBackend):
                def artifact_targets_for_export(self, request):
                    return [target] if request["query_type"] == "query" else []
                def ensure_invoice_packages(self, _request, **_kwargs):
                    raise ValueError("artifact_cancelled")
            snapshot = {"accounts": [{"connection_id": "conn_1", "ready": True, "missing_ranges": [], "total": 1, "cached": {"xml": 0, "html": 0, "pdf": 0}}]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                result = ArtifactBatchCoordinator(Backend(root), {
                    "destination": str(root / "output"), "connection_ids": ["conn_1"],
                    "directions": ["purchase"], "kinds": ["xml"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31", "pdf_concurrency": 1,
                }).run()
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["accounts"]["conn_1"]["status"], "stopped")
            self.assertNotIn("error", result["accounts"]["conn_1"])

    def test_accounts_are_processed_sequentially_and_one_failure_does_not_stop_the_next(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            order = []

            class Backend(FakeBackend):
                def connection_tax_code(self, connection_id):
                    return connection_id

                def artifact_targets_for_export(self, request):
                    connection_id = request["connection_ids"][0]
                    return [{
                        "artifact_key": f"purchase|query|{connection_id}|AA|1|1",
                        "direction": "purchase", "query_type": "query",
                        "nbmst": connection_id, "khhdon": "AA", "shdon": "1",
                        "khmshdon": "1",
                    }] if request["query_type"] == "query" else []

                def ensure_invoice_packages(self, request, **_kwargs):
                    connection_id = request["connection_ids"][0]
                    order.append(connection_id)
                    if connection_id == "first":
                        raise RuntimeError("source failed")

            snapshot = {"accounts": [
                {"connection_id": connection_id, "ready": True, "missing_ranges": [],
                 "total": 1, "cached": {"xml": 0, "html": 0, "pdf": 0}}
                for connection_id in ("first", "second")
            ]}
            with patch.object(ArtifactInspector, "snapshot", return_value=snapshot):
                result = ArtifactBatchCoordinator(Backend(root), {
                    "destination": str(root / "output"),
                    "connection_ids": ["first", "second"],
                    "directions": ["purchase"], "kinds": ["xml"],
                    "date_from": "2026-01-01", "date_to": "2026-01-31",
                    "pdf_concurrency": 5,
                }).run()
            self.assertEqual(order, ["first", "second"])
            self.assertEqual(result["accounts"]["first"]["status"], "error")
            self.assertEqual(result["accounts"]["second"]["status"], "completed")
            self.assertEqual(result["warning_count"], 1)


if __name__ == "__main__":
    unittest.main()
