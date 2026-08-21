"""Offline JSON-RPC transport for the vendored mia-crawl-service runtime.

Electron owns process transport and local file dialogs. Crawl behavior,
accounts/sessions, job admission, recovery, cache/coverage, persistence and
progress are delegated to the source repository through a lazily loaded
ProductionBackend. Keeping that backend off the process bootstrap path lets the
local JSON-RPC host always answer health/storage calls even if a production
crawler dependency later fails to load.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mia_account_purge import purge_account_data, scrub_account_log_lines
from mia_logging import close_logging, configure_logging
from mia_storage import Storage, StorageError

MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = "1.0"
RUNTIME_VERSION = "0.5.0"
storage: Storage | None = None
# Compatibility marker for older tests/state only. The local runtime never
# creates a legacy CrawlerCoordinator or a second crawl worker.
crawler = None
data_directory: Path | None = None
logger = None
production_backend: Any | None = None
# Kept patchable for unit tests while avoiding an eager import of mia_backend.
ProductionBackend = None


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        encoded = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": -32603, "message": "response_too_large"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
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


def _crawler_logger() -> logging.Logger:
    return logging.getLogger("mia_crawler")


def _production_backend_class():
    global ProductionBackend
    if ProductionBackend is None:
        from mia_backend import ProductionBackend as backend_class
        ProductionBackend = backend_class
    return ProductionBackend


def _production_backend():
    global production_backend
    if data_directory is None:
        raise RpcError(-32011, "storage_not_initialized")
    if production_backend is None:
        backend_class = _production_backend_class()
        production_backend = backend_class(data_directory, logger)
        _crawler_logger().info("source_backend_ready runtime_version=%s", RUNTIME_VERSION)
    return production_backend


def _source_error_name(error: BaseException) -> str | None:
    for name in ("error_code", "code"):
        value = getattr(error, name, None)
        if isinstance(value, str) and value:
            return value
    mapping = {
        "AccountBusyError": "account_busy",
        "CapacityExhaustedError": "capacity_exhausted",
        "IdempotencyConflictError": "idempotency_conflict",
        "AccountConnectionNotFoundError": "connection_not_found",
        "ResourceOwnershipError": "resource_not_found",
        "JobNotFoundError": "job_not_found",
    }
    return mapping.get(type(error).__name__)


def _log_job_status(value: dict[str, Any]) -> None:
    current = value.get("current_month") or {}
    error = value.get("error") or {}
    _crawler_logger().info(
        "job_status job_id=%s connection_id=%s status=%s stage=%s overall=%s "
        "message=%s month=%s month_index=%s month_total=%s processed=%s planned=%s "
        "month_percent=%s error_code=%s",
        value.get("job_id"),
        value.get("connection_id"),
        value.get("status"),
        value.get("stage"),
        value.get("overall_percent"),
        value.get("message"),
        current.get("key"),
        current.get("index"),
        current.get("total"),
        current.get("processed"),
        current.get("planned"),
        current.get("percent"),
        error.get("code"),
    )


def _purge_source_account(connection_id: str) -> dict[str, Any]:
    global logger, production_backend
    if data_directory is None:
        raise RpcError(-32011, "storage_not_initialized")
    backend = _production_backend()
    try:
        tax_code = backend.connection_tax_code(connection_id)
    except AttributeError:
        tax_code = backend.connection_username(connection_id)

    backend.close()
    production_backend = None

    result = purge_account_data(data_directory, connection_id, tax_code)
    identifiers = [connection_id, tax_code, *list(result.get("job_ids") or ())]

    close_logging()
    removed_log_lines = scrub_account_log_lines(data_directory, identifiers)
    logger = configure_logging(
        data_directory / "logs", os.environ.get("MIA_RUNTIME_LOG_LEVEL", "INFO")
    )
    logger.info(
        "source_account_purge_completed jobs=%s artifact_files=%s "
        "source_directory=%s log_lines=%s",
        len(result.get("job_ids") or ()),
        int(result.get("artifact_files_removed") or 0),
        bool(result.get("source_directory_removed")),
        removed_log_lines,
    )
    return {
        "deleted": True,
        "jobs_removed": len(result.get("job_ids") or ()),
        "artifact_files_removed": int(result.get("artifact_files_removed") or 0),
        "source_directory_removed": bool(result.get("source_directory_removed")),
        "log_lines_removed": removed_log_lines,
    }


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
            logger = configure_logging(
                data_dir / "logs", os.environ.get("MIA_RUNTIME_LOG_LEVEL", "INFO")
            )
            # mia.sqlite3 remains migration/artifact compatibility storage only.
            # No legacy crawler/thread is constructed from it.
            crawler = None
            logger.info("storage_initialized schema_version=%s", result["schema_version"])
            return result, False
        except StorageError as error:
            raise RpcError(-32010, error.code) from None

    if method.startswith("source.accounts."):
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            backend = _production_backend()
            if method == "source.accounts.create":
                return backend.create_connection(params["username"], params["password"]), False
            if method == "source.accounts.list":
                return backend.list_connections(), False
            if method == "source.accounts.get":
                return backend.get_connection(params["connection_id"]), False
            if method == "source.accounts.reconnect":
                return backend.reconnect_connection(
                    params["connection_id"], params["username"], params["password"]
                ), False
            if method == "source.accounts.purge":
                return _purge_source_account(params["connection_id"]), False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except Exception as error:
            code = _source_error_name(error)
            if logger is not None:
                logger.exception(
                    "source_account_failed method=%s error_type=%s error_code=%s",
                    method, type(error).__name__, code,
                )
            raise RpcError(-32051, code or "source_account_failed") from None

    if method.startswith("source.jobs."):
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            backend = _production_backend()
            if method == "source.jobs.start":
                intent = dict(params).get("intent") or {}
                _crawler_logger().info(
                    "job_start_requested connection_id=%s from=%s to=%s "
                    "force_refresh=%s directions=%s scopes=%s",
                    intent.get("connection_id"),
                    intent.get("date_from"),
                    intent.get("date_to"),
                    bool(intent.get("force_refresh")),
                    ",".join(intent.get("directions") or ()),
                    ",".join(intent.get("scopes") or ()),
                )
                result = backend.start(dict(params))
                _crawler_logger().info(
                    "job_created job_id=%s connection_id=%s status=%s",
                    result.get("job_id"), result.get("connection_id"), result.get("status"),
                )
                return result, False
            if method == "source.jobs.status":
                result = backend.get(params["job_id"])
                _log_job_status(result)
                return result, False
            if method == "source.jobs.summary":
                return backend.summary(params["job_id"]), False
            if method == "source.jobs.resume_all":
                return backend.resume_all(), False
            if method == "source.jobs.latest":
                if hasattr(backend, "latest_all"):
                    return backend.latest_all(), False
                latest: dict[str, Any] = {}
                for job in backend.repository.list_jobs_for_reconciliation():
                    connection_id = str(job.parameters.get("connection_id") or job.account_key)
                    current = latest.get(connection_id)
                    if current is None or (job.created_at, job.job_id) > (
                        current.created_at, current.job_id
                    ):
                        latest[connection_id] = job
                return [backend.public_job(job) for job in latest.values()], False
            if method == "source.jobs.cancel":
                result = backend.cancel(params["job_id"])
                _crawler_logger().warning(
                    "job_cancel_requested job_id=%s status=%s",
                    params["job_id"], result.get("status"),
                )
                return result, False
        except RpcError:
            raise
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except Exception as error:
            code = _source_error_name(error)
            if logger is not None:
                logger.exception(
                    "source_job_failed method=%s error_type=%s error_code=%s",
                    method, type(error).__name__, code,
                )
            raise RpcError(-32070, code or "source_job_failed") from None

    if method == "storage.status":
        if storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            return storage.status(), False
        except StorageError as error:
            raise RpcError(-32010, error.code) from None

    # Legacy DB routes remain data-compatibility helpers only. None starts or
    # controls a crawler. Production React/Electron does not call these routes.
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
                storage.update_account_company(
                    params["account_id"], params["company_name"], params["timestamp"]
                )
                return storage.get_account(params["account_id"]), False
            if method in {"accounts.delete", "accounts.purge"}:
                account = storage.get_account(params["account_id"])
                return purge_account_data(
                    data_directory, params["account_id"], str(account["username"])
                ), False
        except (KeyError, TypeError):
            raise RpcError(-32602, "invalid_params") from None
        except StorageError as error:
            raise RpcError(-32020, error.code) from None
        except (OSError, sqlite3.Error):
            raise RpcError(-32021, "account_purge_failed") from None

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
                return storage.cancel_job(params["job_id"], timestamp), False
            if method == "jobs.transition":
                return storage.transition_job(params), False
            if method == "jobs.clear":
                storage.clear_terminal_jobs()
                return None, False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        except StorageError as error:
            raise RpcError(-32030, error.code) from None

    # The pre-refactor crawler.* RPC surface is intentionally absent. There is
    # exactly one source-owned sequential worker behind source.jobs.*.

    if method == "artifacts.pdf_health":
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                version = browser.version
                browser.close()
            return {"ready": True, "browser": "chromium", "version": version}, False
        except Exception:
            if logger is not None:
                logger.exception("pdf_runtime_unavailable")
            raise RpcError(-32061, "pdf_runtime_unavailable") from None

    if method == "artifacts.export":
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            value = dict(params)
            if value.get("result_scopes"):
                return _production_backend().export_results(value), False
            from mia_artifacts import ArtifactExporter

            if storage is None:
                raise RpcError(-32011, "storage_not_initialized")
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
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            if method in {"results.overview", "results.details"}:
                kind = "overview" if method == "results.overview" else "details"
                result = _production_backend().results(kind, dict(params))
                if logger is not None:
                    logger.info(
                        "results_read connection_id=%s kind=%s from=%s to=%s "
                        "direction=%s rows=%s has_more=%s",
                        params.get("connection_id"),
                        kind,
                        params.get("date_from"),
                        params.get("date_to"),
                        params.get("direction"),
                        len(result.get("items") or ()),
                        bool((result.get("pagination") or {}).get("has_more")),
                    )
                return result, False
            if storage is None:
                raise RpcError(-32011, "storage_not_initialized")
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
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "request_too_large"},
                }
            )
            return 64

        request_id: str | int | None = None
        method: str | None = None
        started = time.perf_counter()
        try:
            decoded = json.loads(raw.decode("utf-8"))
            request_id, method, params = validate_request(decoded)
            if logger is not None:
                logger.info("rpc_start request_id=%s method=%s", request_id, method)
            result, should_stop = dispatch(method, params)
            write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
            if logger is not None:
                logger.info(
                    "rpc_end request_id=%s method=%s outcome=ok duration_ms=%.1f",
                    request_id,
                    method,
                    (time.perf_counter() - started) * 1000,
                )
        except UnicodeDecodeError:
            write_message(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}}
            )
        except json.JSONDecodeError:
            write_message(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}}
            )
        except RpcError as error:
            if logger is not None:
                logger.warning(
                    "rpc_end request_id=%s method=%s outcome=rpc_error code=%s duration_ms=%.1f",
                    request_id,
                    method,
                    error.code,
                    (time.perf_counter() - started) * 1000,
                )
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": error.code, "message": error.message},
                }
            )
        except Exception:
            if logger is not None:
                logger.exception(
                    "rpc_end request_id=%s method=%s outcome=internal_error duration_ms=%.1f",
                    request_id,
                    method,
                    (time.perf_counter() - started) * 1000,
                )
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "internal_error"},
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
