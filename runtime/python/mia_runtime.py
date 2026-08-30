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
import threading
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
_write_lock = threading.Lock()
_production_backend_lock = threading.Lock()
_artifact_task_lock = threading.Lock()
_artifact_task: dict[str, Any] | None = None


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
    with _write_lock:
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
        with _production_backend_lock:
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


def _copy_artifacts(
    value: dict[str, Any], cancel_event: threading.Event | None = None
) -> dict[str, Any]:
    from mia_artifacts import ArtifactExporter

    if storage is None or data_directory is None:
        raise RpcError(-32011, "storage_not_initialized")
    backend = _production_backend()
    backend.prepare_artifacts(value)

    def artifact_progress(event: dict[str, Any]) -> None:
        write_message({
            "jsonrpc": "2.0", "method": "artifact.progress", "params": event,
        })

    if set(value.get("kinds") or ()).intersection({"xml", "html"}):
        source_result = backend.ensure_invoice_packages(
            value,
            progress_callback=artifact_progress,
            cancel_callback=cancel_event.is_set if cancel_event is not None else None,
        )
        value["_artifact_keys"] = source_result["keys"]

    result = ArtifactExporter(storage, data_directory).export(
        value,
        # Package progress is authoritative for per-invoice XML/HTML state.
        # Keep local copy notifications private so the stable notification
        # contract remains compatible with already-running Electron clients.
        progress_callback=None,
        cancel_callback=cancel_event.is_set if cancel_event is not None else None,
    )
    if not result.get("count"):
        raise RpcError(-32062, "artifact_batch_empty")
    return result


def _run_artifact_task(task_id: str, value: dict[str, Any], cancel_event: threading.Event) -> None:
    global _artifact_task
    try:
        if value.get("result_scopes"):
            def export_progress(event: dict[str, Any]) -> None:
                write_message({
                    "jsonrpc": "2.0",
                    "method": "export.progress",
                    "params": event,
                })

            result = _production_backend().export_results(
                value,
                progress_callback=export_progress,
            )
        else:
            result = _copy_artifacts(value, cancel_event)
        status, error = "completed", None
    except ValueError as exc:
        if str(exc) == "artifact_cancelled":
            result, status, error = None, "cancelled", "artifact_cancelled"
        else:
            result, status, error = None, "failed", "invalid_params"
    except RpcError as exc:
        result, status, error = None, "failed", exc.message
    except OSError:
        result, status, error = None, "failed", "artifact_write_failed"
    except Exception:
        if logger is not None:
            logger.exception("artifact_task_failed task_id=%s", task_id)
        result, status, error = None, "failed", "internal_error"
    with _artifact_task_lock:
        if _artifact_task and _artifact_task.get("task_id") == task_id:
            _artifact_task.update(status=status, result=result, error=error)


def _artifact_task_view(task_id: str) -> dict[str, Any]:
    with _artifact_task_lock:
        if not _artifact_task or _artifact_task.get("task_id") != task_id:
            raise RpcError(-32063, "artifact_task_not_found")
        return {
            "task_id": task_id,
            "status": _artifact_task["status"],
            "result": _artifact_task.get("result"),
            "error": _artifact_task.get("error"),
        }


def _run_unified_artifact_task(task_id: str, coordinator: Any) -> None:
    global _artifact_task
    try:
        result = coordinator.run()
        status = str(result.get("status") or "completed")
        error = None
    except Exception:
        result, status, error = None, "failed", "internal_error"
        if logger is not None:
            logger.exception("unified_artifact_task_failed task_id=%s", task_id)
    with _artifact_task_lock:
        if _artifact_task and _artifact_task.get("task_id") == task_id:
            _artifact_task.update(status=status, result=result, error=error)


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
        if (
            not isinstance(params, dict)
            or not isinstance(params.get("data_dir"), str)
            or not isinstance(params.get("reset_desktop_session", False), bool)
        ):
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
            if params.get("reset_desktop_session"):
                from mia_backend import cancel_stale_jobs_for_desktop_session

                cancelled = cancel_stale_jobs_for_desktop_session(data_dir, logger)
                logger.info(
                    "desktop_session_reset active_jobs_cancelled=%s", cancelled
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

    if method == "source.sync.states":
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            if not isinstance(params, dict):
                raise ValueError("invalid_params")
            connection_ids = params.get("connection_ids")
            direction = params.get("direction")
            if (
                not isinstance(connection_ids, list)
                or len(connection_ids) > 500
                or any(not isinstance(item, str) for item in connection_ids)
                or direction not in {"purchase", "sold"}
            ):
                raise ValueError("invalid_params")
            return _production_backend().sync_states(connection_ids, direction), False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None

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

    if method == "artifacts.snapshot":
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            from mia_artifact_pipeline import ArtifactInspector
            return ArtifactInspector(_production_backend()).snapshot(dict(params)), False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None

    if method == "artifacts.coverage":
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            from mia_artifact_pipeline import ArtifactInspector
            return ArtifactInspector(_production_backend()).coverage(dict(params)), False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None

    if method == "artifacts.batch.start":
        global _artifact_task
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            from mia_artifact_pipeline import ArtifactBatchCoordinator
            coordinator = ArtifactBatchCoordinator(
                _production_backend(), dict(params), logger=logger,
            )
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None
        with _artifact_task_lock:
            if _artifact_task and _artifact_task.get("status") in {
                "running", "cancelling"
            }:
                raise RpcError(-32064, "artifact_task_active")
            task_id = f"artifact_{uuid.uuid4().hex}"
            _artifact_task = {
                "task_id": task_id, "status": "running", "result": None,
                "error": None, "coordinator": coordinator,
            }
        threading.Thread(
            target=_run_unified_artifact_task,
            args=(task_id, coordinator),
            name="mia-unified-artifact-export", daemon=True,
        ).start()
        return {"task_id": task_id, "status": "running"}, False

    if method == "artifacts.batch.status":
        task_id = str(params.get("task_id") or "")
        with _artifact_task_lock:
            if not _artifact_task or _artifact_task.get("task_id") != task_id:
                raise RpcError(-32063, "artifact_task_not_found")
            coordinator = _artifact_task.get("coordinator")
            status = _artifact_task["status"]
            error = _artifact_task.get("error")
            result = _artifact_task.get("result")
        view = coordinator.view() if coordinator is not None else (result or {})
        return {"task_id": task_id, **view, "status": status, "error": error}, False

    if method == "artifacts.batch.failures":
        task_id = str(params.get("task_id") or "")
        connection_id = str(params.get("connection_id") or "")
        offset = params.get("offset", 0)
        limit = params.get("limit", 50)
        if (
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or isinstance(limit, bool) or not isinstance(limit, int)
            or limit < 1 or limit > 100
        ):
            raise RpcError(-32602, "invalid_params")
        with _artifact_task_lock:
            if not _artifact_task or _artifact_task.get("task_id") != task_id:
                raise RpcError(-32063, "artifact_task_not_found")
            coordinator = _artifact_task.get("coordinator")
        if coordinator is None:
            raise RpcError(-32063, "artifact_task_not_found")
        try:
            failures, total = coordinator.failure_view(connection_id, offset, limit)
        except ValueError:
            raise RpcError(-32602, "invalid_params") from None
        return {
            "task_id": task_id, "connection_id": connection_id,
            "items": failures, "total": total, "offset": offset, "limit": limit,
        }, False

    if method == "artifacts.batch.cancel":
        task_id = str(params.get("task_id") or "")
        kind = params.get("kind")
        if kind is not None and kind not in {"xml", "html", "pdf"}:
            raise RpcError(-32602, "invalid_params")
        with _artifact_task_lock:
            if not _artifact_task or _artifact_task.get("task_id") != task_id:
                raise RpcError(-32063, "artifact_task_not_found")
            coordinator = _artifact_task.get("coordinator")
        if coordinator is None:
            raise RpcError(-32063, "artifact_task_not_found")
        coordinator.cancel(kind)
        return {"task_id": task_id, "cancelled": True, "kind": kind}, False

    if method == "artifacts.export.start":
        if data_directory is None or storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        value = dict(params)
        kinds = set(value.get("kinds") or ())
        result_export = bool(value.get("result_scopes"))
        if (
            (result_export and kinds != {"excel"})
            or (not result_export and (not kinds or not kinds.issubset({"xml", "html"})))
        ):
            raise RpcError(-32602, "invalid_params")
        with _artifact_task_lock:
            if _artifact_task and _artifact_task.get("status") in {"running", "cancelling"}:
                raise RpcError(-32064, "artifact_task_active")
            task_id = f"artifact_{uuid.uuid4().hex}"
            cancel_event = threading.Event()
            _artifact_task = {
                "task_id": task_id, "status": "running", "result": None,
                "error": None, "cancel_event": cancel_event,
            }
        threading.Thread(
            target=_run_artifact_task,
            args=(task_id, value, cancel_event),
            name="mia-artifact-export",
            daemon=True,
        ).start()
        return {"task_id": task_id, "status": "running"}, False

    if method == "artifacts.targets":
        if data_directory is None or storage is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            value = dict(params)
            keys = _production_backend().artifact_keys_for_export(value)
            return {"keys": sorted(keys), "total": len(keys)}, False
        except (KeyError, TypeError, ValueError):
            raise RpcError(-32602, "invalid_params") from None

    if method == "artifacts.export.status":
        return _artifact_task_view(str(params.get("task_id") or "")), False

    if method == "artifacts.export.cancel":
        task_id = str(params.get("task_id") or "")
        with _artifact_task_lock:
            if not _artifact_task or _artifact_task.get("task_id") != task_id:
                raise RpcError(-32063, "artifact_task_not_found")
            if _artifact_task["status"] == "running":
                _artifact_task["status"] = "cancelling"
                _artifact_task["cancel_event"].set()
            status = _artifact_task["status"]
        return {"task_id": task_id, "status": status}, False

    if method == "artifacts.export":
        if data_directory is None:
            raise RpcError(-32011, "storage_not_initialized")
        try:
            value = dict(params)
            if value.get("result_scopes"):
                def export_progress(event: dict[str, Any]) -> None:
                    # JSON-RPC notification: no request id and no sensitive
                    # invoice/path data. Electron allowlists this method and
                    # forwards only its bounded presentation fields.
                    write_message({
                        "jsonrpc": "2.0",
                        "method": "export.progress",
                        "params": event,
                    })

                return _production_backend().export_results(
                    value,
                    progress_callback=export_progress,
                ), False
            return _copy_artifacts(value), False
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
            if method == "results.reconciliation":
                return _production_backend().reconciliation(dict(params)), False
            if method == "results.facets":
                return _production_backend().result_facets(dict(params)), False
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


def _serve_parallel_control(request_id: str | int, method: str, params: Any) -> None:
    """Keep bounded status/cancel RPCs responsive during expensive work."""
    started = time.perf_counter()
    try:
        if logger is not None:
            logger.info("rpc_start request_id=%s method=%s", request_id, method)
        result, _should_stop = dispatch(method, params)
        write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        if logger is not None:
            logger.info(
                "rpc_end request_id=%s method=%s outcome=ok duration_ms=%.1f",
                request_id, method, (time.perf_counter() - started) * 1000,
            )
    except RpcError as error:
        if logger is not None:
            logger.warning(
                "rpc_end request_id=%s method=%s outcome=rpc_error code=%s duration_ms=%.1f",
                request_id, method, error.code,
                (time.perf_counter() - started) * 1000,
            )
        write_message({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": error.code, "message": error.message},
        })
    except Exception:
        if logger is not None:
            logger.exception(
                "rpc_end request_id=%s method=%s outcome=internal_error duration_ms=%.1f",
                request_id, method, (time.perf_counter() - started) * 1000,
            )
        write_message({
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32603, "message": "internal_error"},
        })


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
            if method in {
                "artifacts.coverage", "artifacts.snapshot",
                "artifacts.batch.status", "artifacts.batch.failures",
                # Starting an export only validates the request and launches a
                # background task. Keep it on the responsive control lane so a
                # slow result query ahead of it cannot make Electron time out
                # after the task has actually started.
                "artifacts.export.start", "artifacts.export.status",
                "artifacts.export.cancel",
                "results.reconciliation",
                "source.jobs.status", "source.jobs.cancel", "source.sync.states",
            }:
                threading.Thread(
                    target=_serve_parallel_control,
                    args=(request_id, method, params),
                    name="mia-control-rpc", daemon=True,
                ).start()
                continue
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
