from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Dict, List

from auth import check_key, verify_key_v2

app = FastAPI()


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
def verify_key_v2_endpoint(req: KeyV2Req):
    try:
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="Key verification failed")
