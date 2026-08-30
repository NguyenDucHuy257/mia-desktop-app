"""Adapter around the byte-for-byte vendored tax-portal crawler."""

from __future__ import annotations

import json
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlCancelled(BaseException):
    pass


def should_download_details(scopes: list[str], overview_count: int) -> bool:
    return "detail" in scopes and overview_count > 0


def verify_account(username: str, password: str, session_factory=None) -> dict[str, str]:
    if session_factory is None:
        from app.services.portal_session import TaxPortalSession
        session_factory = TaxPortalSession
    portal = session_factory(username=username, password=password)
    portal.login()
    company = portal.get_company_info()
    company_name = str(company.get("name") or "").strip()
    if not company_name:
        raise ValueError("missing_company_name")
    return {"company_name": company_name[:300]}


class CrawlerCoordinator:
    def __init__(self, storage, data_dir: Path, logger=None) -> None:
        self.storage = storage
        self.data_dir = data_dir
        self.logger = logger
        self._lock = threading.Lock()
        self._workers: dict[str, tuple[threading.Thread, threading.Event]] = {}

    @staticmethod
    def health() -> dict[str, Any]:
        from app.captcha.solver import CaptchaSolver
        solver = CaptchaSolver()
        return {"ready": True, "model_charset_size": len(solver.charset)}

    def start(self, value: dict[str, Any]) -> dict[str, Any]:
        job_id = value["job_id"]
        with self._lock:
            current = self._workers.get(job_id)
            if current and current[0].is_alive():
                return {"accepted": True, "reused": True}
            cancel = threading.Event()
            worker = threading.Thread(target=self._run, args=(value, cancel), daemon=True, name=f"crawl-{job_id}")
            self._workers[job_id] = (worker, cancel)
            worker.start()
        return {"accepted": True, "reused": False}

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(job_id)
            if not worker:
                return False
            worker[1].set()
            return True

    def _transition(self, job_id: str, status: str, percent: int, stage: str, error=None) -> dict[str, Any]:
        current = self.storage.get_job(job_id)
        if current["status"] == status:
            return current
        return self.storage.transition_job({
            "job_id": job_id, "expected_sequence": current["event_sequence"], "status": status,
            "overall_percent": percent, "stage": stage, "timestamp": utc_now(), "error": error,
        })

    def _run(self, value: dict[str, Any], cancel: threading.Event) -> None:
        job_id = value["job_id"]
        password = value.pop("password")
        failure_stage = "startup"
        try:
            from app.config.crawl_config import CrawlConfig
            from app.crawlers.invoice_crawler import InvoiceCrawler, adaptive_paging_options_from_config
            from app.crawlers.invoice_detail_crawler import InvoiceDetailCrawler
            from app.repositories.invoice_detail_repository import InvoiceDetailRepository
            from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
            from app.repositories.invoice_package_repository import InvoicePackageRepository
            from app.crawlers.invoice_package_crawler import InvoicePackageCrawler
            from app.services.invoice_detail_download_service import InvoiceDetailDownloadService
            from app.services.invoice_detail_storage_service import InvoiceDetailStorageService
            from app.services.invoice_overview_storage_service import InvoiceOverviewStorageService
            from app.services.invoice_package_download_service import InvoicePackageDownloadService
            from app.services.invoice_package_storage_service import InvoicePackageStorageService
            from app.services.invoice_pdf_export_service import InvoicePdfExportService
            from app.exporters.invoice_pdf_renderer import InvoicePdfRenderer
            from app.services.overview_downloader import OverviewDownloader
            from app.services.portal_session import TaxPortalSession

            failure_stage = "authentication"
            self._transition(job_id, "running", 2, "authenticating")
            portal = TaxPortalSession(username=value["username"], password=password)
            portal.login()
            company = portal.get_company_info()
            self.storage.update_account_company(value["connection_id"], str(company.get("name") or "").strip(), utc_now())
            if cancel.is_set():
                self._transition(job_id, "cancelling", 2, "cancelling")
                self._transition(job_id, "cancelled", 2, "cancelled")
                return

            intent = value["intent"]
            config = CrawlConfig.from_env()
            def cancellable_get(*args, **kwargs):
                if cancel.is_set():
                    raise CrawlCancelled("cancelled")
                return portal.get(*args, **kwargs)
            crawler = InvoiceCrawler(
                portal.client, request_get=cancellable_get, reauthenticate=portal.login,
                paging_options=adaptive_paging_options_from_config(
                    config.overview, authentication_attempts=config.common.authentication_attempts, profile=config.profile,
                ),
            )
            raw_root = self.data_dir / "crawler-data"
            output_root = self.data_dir / "exports" / job_id
            jobs = [(direction, query_type) for direction in intent["directions"] for query_type in intent["query_types"]]
            for index, (direction, query_type) in enumerate(jobs):
                if cancel.is_set():
                    self._transition(job_id, "cancelling", max(2, int(index * 90 / len(jobs))), "cancelling")
                    self._transition(job_id, "cancelled", max(2, int(index * 90 / len(jobs))), "cancelled")
                    return
                failure_stage = "overview"
                category = "electronic" if query_type == "query" else "cash_register"
                downloader = OverviewDownloader(
                    crawler=crawler, headers_provider=lambda: portal.headers,
                    template_dir=VENDOR_ROOT / "resources" / "templates",
                    storage_service=InvoiceOverviewStorageService(raw_root), company_tax_code=value["username"],
                )
                downloader.download(
                    begin_date=date.fromisoformat(intent["date_from"]), end_date=date.fromisoformat(intent["date_to"]),
                    output_dir=output_root, directions=(direction,), categories=(category,), overwrite=True,
                )
                overview_count = self._import_raw(value["connection_id"], value["username"], raw_root, direction, query_type)
                if should_download_details(intent["scopes"], overview_count):
                    failure_stage = "detail"
                    database = raw_root / value["username"] / "db" / "invoices.sqlite3"
                    detail_repository = InvoiceDetailRepository(database)
                    detail_service = InvoiceDetailDownloadService(
                        detail_crawler=InvoiceDetailCrawler(portal.client, request_get=cancellable_get),
                        overview_repository=InvoiceOverviewRepository(database),
                        detail_storage_service=InvoiceDetailStorageService(raw_root, detail_repository),
                        detail_repository=detail_repository,
                    )
                    detail_service.download_invoice_details(
                        headers=portal.headers, company_tax_code=value["username"], direction=direction,
                        query_type=query_type, from_date=intent["date_from"], to_date=intent["date_to"],
                    )
                    self._import_details(value["connection_id"], value["username"], raw_root, direction, query_type)
                data_types = set(intent.get("data_types") or ())
                wants_pdf = "pdf" in data_types
                if data_types.intersection({"xml", "html", "pdf"}):
                    failure_stage = "artifact"
                    database = raw_root / value["username"] / "db" / "invoices.sqlite3"
                    package_repository = InvoicePackageRepository(database)
                    package_repository.init_db()
                    package_service = InvoicePackageDownloadService(
                        package_crawler=InvoicePackageCrawler(portal.client, request_get=cancellable_get),
                        package_repository=package_repository,
                        storage_service=InvoicePackageStorageService(raw_root, package_repository, VENDOR_ROOT / "resources" / "invoice_assets"),
                    )
                    package_service.download_invoice_packages(
                        headers=portal.headers, company_tax_code=value["username"], direction=direction,
                        query_type=query_type, from_date=intent["date_from"], to_date=intent["date_to"],
                        export_xml="xml" in data_types, export_html="html" in data_types or wants_pdf,
                    )
                    if wants_pdf:
                        html_assets = raw_root / value["username"] / "exports" / "invoice_packages" / direction / query_type / f'{intent["date_from"]}_{intent["date_to"]}' / "html"
                        pdf_service = InvoicePdfExportService(
                            raw_root, package_repository, renderer_factory=lambda: InvoicePdfRenderer(assets_dir=html_assets),
                        )
                        pdf_service.export_invoice_pdfs(
                            company_tax_code=value["username"], direction=direction, query_type=query_type,
                            from_date=intent["date_from"], to_date=intent["date_to"], overwrite=True,
                        )
                percent = 5 + int((index + 1) * 90 / len(jobs))
                self.storage.update_job_progress(job_id, percent, f"overview:{direction}:{query_type}", utc_now())
            self._transition(job_id, "completed", 100, "completed")
        except CrawlCancelled:
            try:
                current = self.storage.get_job(job_id)
                if current["status"] != "cancelling":
                    self._transition(job_id, "cancelling", current["overall_percent"], "cancelling")
                self._transition(job_id, "cancelled", current["overall_percent"], "cancelled")
            except Exception:
                pass
        except Exception as error:
            safe_code = {
                "authentication": "portal_auth_failed", "overview": "overview_failed",
                "detail": "detail_failed", "artifact": "artifact_failed",
            }.get(failure_stage, "crawler_runtime_unavailable")
            if self.logger is not None:
                self.logger.error("crawler_job_failed stage=%s error_type=%s", failure_stage, type(error).__name__)
            try:
                current = self.storage.get_job(job_id)
                if current["status"] == "cancelling" or cancel.is_set():
                    self._transition(job_id, "cancelled", current["overall_percent"], "cancelled")
                elif current["status"] not in {"failed", "cancelled"}:
                    self._transition(job_id, "failed", current["overall_percent"], "failed", {"code": safe_code})
            except Exception:
                pass
        finally:
            password = ""
            value.pop("username", None)
            with self._lock:
                self._workers.pop(job_id, None)

    def _import_raw(self, connection_id: str, tax_code: str, root: Path, direction: str, query_type: str) -> int:
        raw_dir = root / tax_code / "raw" / "invoice_lists" / direction / query_type
        items = []
        for raw_file in sorted(raw_dir.glob("*.json")):
            document = json.loads(raw_file.read_text(encoding="utf-8"))
            for payload in document.get("datas", []):
                parts = [str(payload.get(key, "")) for key in ("nbmst", "khhdon", "shdon", "khmshdon")]
                if all(parts):
                    items.append({"direction": direction, "business_key": "|".join(parts), "payload": payload})
        for offset in range(0, len(items), 5000):
            self.storage.import_overviews({
                "connection_id": connection_id, "items": items[offset:offset + 5000], "timestamp": utc_now(),
            })
        return len({item["business_key"] for item in items})

    def _import_details(self, connection_id: str, tax_code: str, root: Path, direction: str, query_type: str) -> None:
        detail_root = root / tax_code / "raw" / "invoice_details" / direction / query_type
        items = []
        for raw_file in sorted(detail_root.glob("**/*.json")):
            document = json.loads(raw_file.read_text(encoding="utf-8"))
            key = document.get("invoice_key") or {}
            parts = [str(key.get(name, "")) for name in ("nbmst", "khhdon", "shdon", "khmshdon")]
            if all(parts):
                items.append({
                    "direction": direction, "business_key": "|".join(parts),
                    "line_key": "detail", "payload": document.get("detail") or {},
                })
        for offset in range(0, len(items), 10000):
            self.storage.import_details({"connection_id": connection_id, "items": items[offset:offset + 10000]})
