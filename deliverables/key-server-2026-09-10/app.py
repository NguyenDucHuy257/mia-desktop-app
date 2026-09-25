import logging
import secrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Dict, List

from auth import check_key, verify_key_v2
from mia_recovery import RecoveryError, recovery_service

app = FastAPI()
recovery_logger = logging.getLogger("mia.recovery")


class KeyReq(BaseModel):
    tool: str


class KeyV2Req(BaseModel):
    tool: str
    key: str
    device_id: str
    phone: str = ""
    hardware: Dict[str, str] = Field(default_factory=dict)
    legacy_keys: List[str] = Field(default_factory=list)
    # Optional operation context used by MIA 4.0.1+ for account quota and TEST policy.
    mst: str = ""
    date_from: str = ""
    date_to: str = ""
    current_version: str = ""
    # Recovery actions share the already-published /verify-key-v2 endpoint.
    # Empty/"verify" preserves the contract used by every older client.
    action: str = "verify"
    email: str = ""
    challenge_id: str = ""
    code: str = ""


def _verified_device(req: KeyV2Req) -> str:
    result = verify_key_v2(
        req.tool, key=req.key, device_id=req.device_id, phone=req.phone,
        hardware=req.hardware, legacy_keys=req.legacy_keys,
        current_version=req.current_version,
    )
    if not result.get("valid") or result.get("expired") or result.get("reason") != "ok":
        raise RecoveryError("recovery_license_invalid", 403)
    return str(result.get("device_id") or req.device_id)


def _recovery_call(callback, *, action: str, request_id: str):
    started = time.monotonic()
    try:
        result = callback()
        recovery_logger.info(
            "recovery_action_success action=%s request_id=%s elapsed_ms=%d",
            action, request_id, round((time.monotonic() - started) * 1000),
        )
        if isinstance(result, dict):
            result.setdefault("request_id", request_id)
        return result
    except RecoveryError as exc:
        recovery_logger.warning(
            "recovery_action_failed action=%s request_id=%s code=%s status=%d elapsed_ms=%d",
            action, request_id, exc.code, exc.status,
            round((time.monotonic() - started) * 1000),
        )
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "request_id": request_id})
    except ValueError as exc:
        recovery_logger.warning(
            "recovery_action_failed action=%s request_id=%s code=recovery_license_invalid error_type=%s elapsed_ms=%d",
            action, request_id, type(exc).__name__, round((time.monotonic() - started) * 1000),
        )
        raise HTTPException(status_code=400, detail={"code": "recovery_license_invalid", "request_id": request_id})
    except Exception as exc:
        recovery_logger.exception(
            "recovery_action_failed action=%s request_id=%s code=internal_error error_type=%s elapsed_ms=%d",
            action, request_id, type(exc).__name__, round((time.monotonic() - started) * 1000),
        )
        raise HTTPException(status_code=500, detail={"code": "internal_error", "request_id": request_id})


@app.post("/verify-key", response_class=PlainTextResponse)
def verify_key(req: KeyReq):
    """Legacy endpoint kept unchanged for old desktop builds."""
    try:
        return check_key(req.tool)
    except Exception:
        # Preserve the legacy endpoint behavior exactly: old clients only
        # depend on HTTP 200 + plain-text body for valid tool names.
        raise HTTPException(status_code=400)


@app.post("/verify-key-v2")
def verify_key_v2_endpoint(req: KeyV2Req, request: Request = None):
    try:
        action = str(req.action or "verify").strip().lower()
        if action != "verify":
            recovery_request_id = secrets.token_hex(8)
            def perform_recovery_action():
                requester = request.client.host if request and request.client else "unknown"
                device_id = _verified_device(req)
                if action == "contact_request":
                    return recovery_service().request_contact(device_id, req.email, requester)
                if action == "contact_confirm":
                    return recovery_service().confirm(req.challenge_id, req.code, "contact", device_id)
                if action == "password_reset_request":
                    return recovery_service().request_reset(device_id, requester)
                if action == "password_reset_verify":
                    return recovery_service().confirm(req.challenge_id, req.code, "password_reset", device_id)
                raise RecoveryError("invalid_recovery_action")

            return _recovery_call(
                perform_recovery_action, action=action, request_id=recovery_request_id,
            )
        result = verify_key_v2(
            req.tool,
            key=req.key,
            device_id=req.device_id,
            phone=req.phone,
            hardware=req.hardware,
            legacy_keys=req.legacy_keys,
            mst=req.mst,
            date_from=req.date_from,
            date_to=req.date_to,
            current_version=req.current_version,
        )
        # Keep one stable response contract for pending/expired/rejected
        # branches as well as active verification branches.
        result.setdefault("license_valid", bool(result.get("valid")))
        result.setdefault("authorized", bool(result.get("valid")))
        result.setdefault("license_policy", "")
        result.setdefault("license_type", "")
        result.setdefault("mst_limit", None)
        result.setdefault("mst_used", None)
        result.setdefault("mst_remaining", None)
        result.setdefault("mst_authorized", None)
        result.setdefault("mst_newly_bound", False)
        result.setdefault("date_authorized", None)
        result.setdefault("test_month", None)
        result.setdefault("entitlements", None)
        result.setdefault("update", {
            "available": False, "url": "", "label": "",
            "latest_version": "", "current_version": req.current_version,
        })
        return result
    except HTTPException:
        # Recovery dispatch intentionally returns precise 4xx/429/503 errors.
        # Do not collapse those into the generic key-verification 500 below.
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Key verification failed")
