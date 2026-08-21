"""Minimal offline runtime used to validate the Electron/Python boundary."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mia_logging import configure_logging
from mia_storage import Storage, StorageError
from mia_crawler import CrawlerCoordinator, verify_account
from mia_backend import ProductionBackend

MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = "1.0"
RUNTIME_VERSION = "0.4.1"
storage: Storage | None = None
crawler: CrawlerCoordinator | None = None
data_directory: Path | None = None
logger = None
production_backend: ProductionBackend | None = None


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        encoded = json.dumps({
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32603, "message": "response_too_large"},
        }, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def validate_request(value: Any) -> tuple[str | int, str, Any]:
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise RpcError(-32600, "invalid_request")
    request_id = value.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise RpcError(-32600, "invalid_request_id")
    method = value.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(-32600, "invalid_method")
    params = value.get("params", {})
    if not isinstance(params, (dict, list)):
        raise RpcError(-32602, "invalid_params")
    return request_id, method, params


def _production_backend() -> ProductionBackend:
    global production_backend
    if data_directory is None:
        raise RpcError(-32011, "storage_not_initialized")
    if production_backend is None:
        production_backend = ProductionBackend(data_directory, logger)
    return production_backend


def dispatch(method: str, params: Any) -> tuple[Any, bool]:
    global storage, crawler, data_directory, logger, production_backend
    if method == "system.health":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "pid": os.getpid(),
        }, False
    if method == "system.echo":
        return params, False
    if method == "system.sleep":
        if not isinstance(params, dict) or isinstance(params.get("milliseconds"), bool):
            raise RpcError(-32602, "invalid_params")
        milliseconds = params.get("milliseconds")
        if not isinstance(milliseconds, int) or not 0 <= milliseconds <= 5000:
            raise RpcError(-32602, "invalid_duration")
        time.sleep(milliseconds / 1000)
        return {"slept_ms": milliseconds}, False
    if method == "system.shutdown":
        if production_backend is not None:
            production_backend.close()
            production_backend = None
        return {"accepted": True}, True
    if method == "storage.initialize":
        if not isinstance(params, dict) or not isinstance(params.get("data_dir"), str):
            raise RpcError(-32602, "invalid_params")
        data_dir = Path(params["data_dir"])
        if not data_dir.is_absolute():
            raise RpcError(-32602, "data_dir_not_absolute")
        storage = Storage(data_dir / "mia.sqlite3")
        try:
            result = storage.initialize()
            data_directory = data_dir
            logger = configure_logging(data_dir / "logs", os.environ.get("MIA_RUNTIME_LOG_LEVEL", "INFO"))
            crawler = CrawlerCoordinator(storage, data_dir, logger)
            logger.info("storage_initialized schema_version=%s", result["schema_version"])
            return result, False
        except StorageError as error:
            raise RpcError(-32010, error.code) from None
    if method.startswith("source.jobs."):
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            backend = _production_backend()
            if method == "source.jobs.start":
                return backend.start(dict(params)), False
            if method == "source.jobs.status":
                return backend.get(params["job_id"]), False
            if method == "source.jobs.summary":
                return backend.summary(params["job_id"]), False
            if method == "source.jobs.resume_all":
                return backend.resume_all(), False
            if method == "source.jobs.cancel":
                return backend.cancel(params["job_id"]), False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except Exception as error:
            if logger is not None:
                logger.error("production_backend_failed method=%s error_type=%s", method, type(error).__name__)
            raise RpcError(-32070, "production_backend_failed") from None
    if method == "storage.status":
        if storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            return storage.status(), False
        except StorageError as error:
            raise RpcError(-32010, error.code) from None
    if method.startswith("accounts."):
        if storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            if method == "accounts.create":
                return storage.create_account(params), False
            if method == "accounts.list":
                return storage.list_accounts(), False
            if method == "accounts.get":
                return storage.get_account(params["account_id"]), False
            if method == "accounts.secret":
                return storage.get_account_secret(params["account_id"]), False
            if method == "accounts.update":
                return storage.update_account(params), False
            if method == "accounts.update_company":
                storage.update_account_company(params["account_id"], params["company_name"], params["timestamp"])
                return storage.get_account(params["account_id"]), False
            if method == "accounts.delete":
                storage.delete_account(params["account_id"])
                return None, False
        except (KeyError, TypeError):
            raise RpcError(-32602, "invalid_params") from None
        except StorageError as error:
            raise RpcError(-32020, error.code) from None
    if method.startswith("jobs."):
        if storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            if method == "jobs.start":
                value = dict(params)
                value["job_id"] = value.get("job_id") or f"job_{uuid.uuid4()}"
                value["timestamp"] = value.get("timestamp") or datetime.now(timezone.utc).isoformat()
                return storage.create_job(value), False
            if method == "jobs.resume":
                return storage.resume_job(), False
            if method == "jobs.resume_all":
                return storage.resume_jobs(), False
            if method == "jobs.status":
                return storage.get_job(params["job_id"]), False
            if method == "jobs.summary":
                return storage.job_summary(params["job_id"]), False
            if method == "jobs.cancel":
                timestamp = params.get("timestamp") or datetime.now(timezone.utc).isoformat()
                result = storage.cancel_job(params["job_id"], timestamp)
                if crawler is not None:
                    crawler.cancel(params["job_id"])
                return result, False
            if method == "jobs.transition":
                return storage.transition_job(params), False
            if method == "jobs.clear":
                storage.clear_terminal_jobs()
                return None, False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except StorageError as error:
            raise RpcError(-32030, error.code) from None
    if method == "crawler.start":
        if storage is None or crawler is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            required = ("job_id", "connection_id", "username", "password", "intent")
            if not isinstance(params, dict) or any(key not in params for key in required):
                raise RpcError(-32602, "invalid_params")
            return crawler.start(dict(params)), False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
    if method == "crawler.health":
        if crawler is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            return crawler.health(), False
        except Exception:
            if logger is not None:
                logger.exception("crawler_health_failed")
            raise RpcError(-32050, "crawler_runtime_unavailable") from None
    if method == "crawler.verify_account":
        if (not isinstance(params, dict)
                or not isinstance(params.get("username"), str)
                or re.fullmatch(r"\d{10}(?:-\d{3})?", params["username"]) is None
                or not isinstance(params.get("password"), str)
                or not 1 <= len(params["password"]) <= 256):
            raise RpcError(-32602, "invalid_params")
        if storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            return verify_account(params["username"], params["password"]), False
        except Exception:
            if logger is not None:
                logger.warning("portal_account_verification_failed")
            raise RpcError(-32051, "authentication_failed") from None
    if method == "artifacts.pdf_health":
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                version = browser.version
                browser.close()
            return {"ready": True, "browser": version}, False
        except Exception:
            if logger is not None:
                logger.exception("pdf_runtime_unavailable")
            raise RpcError(-32061, "pdf_runtime_unavailable") from None
    if method == "artifacts.export":
        if storage is None or data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            value = dict(params)
            if value.get("result_scopes"):
                return _production_backend().export_results(value), False
            from mia_artifacts import ArtifactExporter
            if production_backend is not None:
                production_backend.prepare_artifacts(value)
            return ArtifactExporter(storage, data_directory).export(value), False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except OSError:
            raise RpcError(-32060, "artifact_write_failed") from None
    if method == "artifacts.list":
        if storage is None or data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            from mia_artifacts import ArtifactExporter
            return ArtifactExporter(storage, data_directory).list(dict(params)), False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
    if method.startswith("results."):
        if storage is None or data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            if method in {"results.overview", "results.details"}:
                kind = "overview" if method == "results.overview" else "details"
                return _production_backend().results(kind, dict(params)), False
            if method == "results.import_overviews":
                return storage.import_overviews(params), False
            if method == "results.import_details":
                return storage.import_details(params), False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except StorageError as error:
            raise RpcError(-32040, error.code) from None
    raise RpcError(-32601, "method_not_found")


def serve() -> int:
    should_stop = False
    while not should_stop:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 2)
        if not raw:
            break
        if len(raw) > MAX_MESSAGE_BYTES + 1 or not raw.endswith(b"\n"):
            write_message({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "request_too_large"},
            })
            return 64

        request_id: str | int | None = None
        try:
            decoded = json.loads(raw.decode("utf-8"))
            request_id, method, params = validate_request(decoded)
            result, should_stop = dispatch(method, params)
            write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        except UnicodeDecodeError:
            write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}})
        except json.JSONDecodeError:
            write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}})
        except RpcError as error:
            write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": error.message}})
        except Exception:
            write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "internal_error"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
