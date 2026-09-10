from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
_LOCK = threading.RLock()


@contextmanager
def _process_lock(lock_path: Path):
    """Serialize license mutations across multiple Uvicorn/Gunicorn workers.

    The server is Linux in production. If fcntl is unavailable for any reason,
    the in-process RLock still protects a single worker.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        handle.close()

TOOL_PATHS = {
    "MIA": "MIA/vip.txt",
    "MIA2": "MIA2/vip.txt",
    "MIA3": "MIA3/vip.txt",
    "GBOT": "GBOT/vip.txt",
    "IDQUICK": "IDQUICK/vip.txt",
    "GSOFT": "GSOFT/vip.txt",
}

HARDWARE_FIELDS = {
    "system_uuid",
    "bios_serial",
    "baseboard_serial",
    "machine_guid",
    "cpu_id",
    "disk_serial",
}
MIN_HARDWARE_FIELDS = 3
MIN_MATCH_RATIO = 0.50
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
NEW_KEY_PREFIX = "KEYV2-"
V2_TOOLS = {"GSOFT", "MIA"}
MIA_V1_RE = re.compile(r"^key[0-9a-fA-F]{29}$")
MIA_V2_PHONE_RE = re.compile(r"^KEY[0-9a-fA-F]{29}0[0-9]{9}$")
POLICY_RE = re.compile(r"^(VIP|TEST)([1-9][0-9]*)?$", re.IGNORECASE)
SEMVER_RE = re.compile(r"(?<![0-9])(\d+)\.(\d+)\.(\d+)(?![0-9])")
MST_RE = re.compile(r"^(?:[0-9]{10}(?:-[0-9]{3})?|[0-9]{12})$")
PHONE_RE = re.compile(r"^0[0-9]{9}$")
TEST_DATE_FROM = "2026-08-01"
TEST_DATE_TO = "2026-08-31"


def _path(relative: str) -> Path:
    return BASE_DIR / relative


def _tool_path(tool: str) -> Path:
    relative = TOOL_PATHS.get(str(tool or "").upper())
    if not relative:
        raise ValueError("Invalid tool")
    return _path(relative)


def load_keys(tool: str) -> str:
    return _tool_path(tool).read_text(encoding="utf-8")


def check_key(tool: str) -> str:
    # Kept intact so every old desktop build continues to work during migration.
    return load_keys(tool)


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.rstrip("\r\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_json(path: Path, payload: dict) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: Path) -> dict:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _find_key_line(lines: Iterable[str], key: str) -> Optional[str]:
    for line in lines:
        parts = line.split("|")
        if parts and parts[0].strip() == key:
            return line
    return None


def _expiry_from_line(line: str) -> str:
    parts = line.split("|")
    if len(parts) > 1 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", parts[1].strip()):
        return parts[1].strip()
    return parts[2].strip() if len(parts) > 2 else ""


def _is_expired(expiry: str) -> bool:
    if not expiry:
        return False
    try:
        return datetime.strptime(expiry, "%d/%m/%Y").date() < datetime.now().date()
    except ValueError:
        # Malformed expiry must never silently become an unlimited license.
        return True


def _clean_phone(phone: str) -> str:
    value = re.sub(r"\s+", "", str(phone or "").strip())
    if (not PHONE_RE.fullmatch(value) or value == "0000000000"
            or len(set(value)) == 1 or "|" in value
            or "\r" in value or "\n" in value):
        return ""
    return value


def _sanitize_hardware(hardware: Dict[str, str]) -> Dict[str, str]:
    clean = {}
    for name, value in dict(hardware or {}).items():
        if name not in HARDWARE_FIELDS:
            continue
        value = str(value or "").strip().lower()
        if HASH_RE.fullmatch(value):
            clean[name] = value
    if len(clean) < MIN_HARDWARE_FIELDS:
        raise ValueError(
            f"Not enough hardware signals: need at least {MIN_HARDWARE_FIELDS}/{len(HARDWARE_FIELDS)}"
        )
    return clean


def _hardware_match(saved: Dict[str, str], current: Dict[str, str]) -> tuple[bool, float, int]:
    saved = {k: v for k, v in dict(saved or {}).items() if k in HARDWARE_FIELDS and v}
    if len(saved) < MIN_HARDWARE_FIELDS:
        return False, 0.0, 0
    matches = sum(1 for name, value in saved.items() if current.get(name) == value)
    ratio = matches / len(saved)
    return matches >= MIN_HARDWARE_FIELDS and ratio >= MIN_MATCH_RATIO, ratio, matches


def _new_key(tool: str, device_id: str, phone: str) -> str:
    phone = _clean_phone(phone)
    if not phone:
        raise ValueError("Missing phone")
    digest = hashlib.sha256(f"{tool.upper()}|{device_id}".encode("utf-8")).hexdigest()
    return f"{NEW_KEY_PREFIX}{digest[:32]}-{phone}"


def _interim_v2_key(tool: str, device_id: str) -> str:
    """KEY2 format used by the short-lived first v2 package."""
    digest = hashlib.sha256(f"{tool.upper()}|{device_id}".encode("utf-8")).hexdigest()
    return "KEY2" + digest[:40]


def _v2_paths(tool: str) -> dict[str, Path]:
    """Return isolated V2 state paths for supported tools.

    GSOFT keeps exactly the same files it already used. MIA receives the same
    V2 state layout under /opt/keys_app/MIA without touching other tool data.
    """
    tool = str(tool or "").upper().strip()
    if tool not in V2_TOOLS:
        raise ValueError("verify-key-v2 unsupported tool")
    folder = _path(tool)
    result = {
        "vip": folder / "vip.txt",
        "legacy": folder / "legacy_vip.txt",
        "bindings": folder / "device_bindings.json",
        "migrations": folder / "legacy_migrations.json",
        "mst_bindings": folder / "mst_bindings.txt",
        "lock": folder / ".license_v2.lock",
    }
    if tool == "MIA":
        # Supplied MIA 3.9.0 source calls tool=MIA2. Read that legacy registry
        # only as a migration source; never mutate MIA2 state.
        result["legacy_sources"] = [_path("MIA2/vip.txt")]
    else:
        result["legacy_sources"] = []
    return result




def _policy_from_line(line: str) -> dict:
    """Parse field #2 of vip.txt into a stable license policy.

    Legacy ``v`` remains unlimited. New limited policies are VIP<N> and
    TEST<N>. Invalid non-empty values are rejected instead of silently
    becoming unlimited.
    """
    parts = line.split("|")
    raw = parts[1].strip() if len(parts) > 1 else ""
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw):
        # Old key|expiry|l|contact|scope layout is a paid legacy license.
        return {"raw": "v", "type": "VIP_UNLIMITED", "limit": None, "test": False}
    if raw.lower() == "v":
        return {"raw": raw or "v", "type": "VIP_UNLIMITED", "limit": None, "test": False}
    if raw.upper() == "VIP":
        # Bare VIP rows predate numbered quota policies. Keep them compatible
        # and unlimited; VIP1/VIP2 remain explicitly limited.
        return {"raw": "VIP", "type": "VIP_UNLIMITED", "limit": None, "test": False}
    match = POLICY_RE.fullmatch(raw)
    if not match:
        raise ValueError("Invalid license policy")
    kind = match.group(1).upper()
    limit = int(match.group(2) or 1)
    return {"raw": f"{kind}{limit}", "type": kind, "limit": limit, "test": kind == "TEST"}


def _normalize_mst(mst: str) -> str:
    value = re.sub(r"\s+", "", str(mst or "").strip())
    if value.isdigit() and len(value) == 13:
        value = value[:10] + "-" + value[10:]
    return value if MST_RE.fullmatch(value) else ""


def _declared_msts(line: str) -> tuple[List[str], bool]:
    """Read only field #5. Never mistake contact phones/notes for an MST."""
    parts = line.split("|")
    scope = parts[4].strip() if len(parts) > 4 else ""
    if not scope or scope.lower() == "o":
        return [], True
    values = []
    for item in scope.split(","):
        normalized = _normalize_mst(item)
        if not normalized:
            raise ValueError("Invalid MST scope")
        if normalized not in values:
            values.append(normalized)
    return values, False


def _key_hash(key: str) -> str:
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()


def _parse_binding_line(line: str) -> Optional[dict]:
    parts = line.split("|")
    if len(parts) < 5:
        return None
    key_hash, key_last4, policy, mst, bound_at = [part.strip() for part in parts[:5]]
    if not HASH_RE.fullmatch(key_hash):
        return None
    normalized_mst = _normalize_mst(mst)
    if not normalized_mst:
        return None
    return {
        "key_hash": key_hash.lower(),
        "key_last4": key_last4,
        "policy": policy,
        "mst": normalized_mst,
        "bound_at": bound_at,
    }


def _binding_state(paths: dict[str, Path], key: str) -> tuple[List[str], List[dict], set[str]]:
    lines = _read_lines(paths["mst_bindings"])
    records = [record for line in lines if (record := _parse_binding_line(line))]
    kh = _key_hash(key)
    msts = {record["mst"] for record in records if record["key_hash"] == kh}
    return lines, records, msts


def _mst_policy_state(paths: dict[str, Path], key: str, policy: dict, mst: str = "") -> dict:
    """Return quota state and atomically bind a new MST when allowed.

    Caller must already hold both _LOCK and _process_lock(paths['lock']).
    """
    limit = policy["limit"]
    normalized_mst = _normalize_mst(mst) if mst else ""
    lines, _records, bound_msts = _binding_state(paths, key)
    declared_msts = list(policy.get("declared_msts") or [])
    effective_msts = set(declared_msts) if declared_msts else bound_msts
    used = len(effective_msts) if limit is not None else 0

    state = {
        "license_policy": policy["raw"],
        "license_type": policy["type"],
        "mst_limit": limit,
        "mst_used": used if limit is not None else None,
        "mst_remaining": max(limit - used, 0) if limit is not None else None,
        "mst_authorized": True if not mst else None,
        "mst_newly_bound": False,
    }
    if limit is None:
        state["mst_authorized"] = not declared_msts or not mst or normalized_mst in effective_msts
        if state["mst_authorized"] is False:
            state["authorization_reason"] = "mst_not_authorized"
        return state
    if used > limit:
        state["mst_authorized"] = False
        state["authorization_reason"] = "license_policy_invalid"
        return state
    if not mst:
        state["mst_authorized"] = None
        return state
    if not normalized_mst:
        state["mst_authorized"] = False
        state["authorization_reason"] = "invalid_mst"
        return state
    if normalized_mst in effective_msts:
        state["mst_authorized"] = True
        return state
    if declared_msts:
        state["mst_authorized"] = False
        state["authorization_reason"] = "mst_not_authorized"
        return state
    if used >= limit:
        state["mst_authorized"] = False
        state["authorization_reason"] = "mst_limit_reached"
        return state

    now = datetime.now().isoformat(timespec="seconds")
    record = "|".join([
        _key_hash(key),
        str(key or "")[-4:],
        policy["raw"],
        normalized_mst,
        now,
    ])
    lines.append(record)
    _atomic_write(paths["mst_bindings"], "\n".join(lines) + "\n")
    used += 1
    state.update({
        "mst_used": used,
        "mst_remaining": max(limit - used, 0),
        "mst_authorized": True,
        "mst_newly_bound": True,
    })
    return state


def _parse_business_date(value: str):
    value = str(value or "").strip()
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    return None


def _test_date_state(policy: dict, date_from: str = "", date_to: str = "") -> dict:
    if not policy["test"]:
        return {"date_authorized": True, "test_month": None}
    if not date_from and not date_to:
        return {"date_authorized": None, "test_month": 8}
    start = _parse_business_date(date_from)
    end = _parse_business_date(date_to)
    if start is None or end is None or start > end:
        return {"date_authorized": False, "test_month": 8, "authorization_reason": "invalid_date_range"}
    lower = datetime.strptime(TEST_DATE_FROM, "%Y-%m-%d").date()
    upper = datetime.strptime(TEST_DATE_TO, "%Y-%m-%d").date()
    allowed = lower <= start <= end <= upper
    return {
        "date_authorized": allowed,
        "test_month": 8,
        **({} if allowed else {"authorization_reason": "test_month_restricted"}),
    }


def _entitlements_from_state(line: str, paths: dict[str, Path], key: str, policy: dict) -> dict:
    declared_msts, unlimited_scope = _declared_msts(line)
    _lines, _records, bound_msts = _binding_state(paths, key)
    allowed = declared_msts or sorted(bound_msts)
    limit = policy["limit"]
    if limit is not None and (not allowed or len(allowed) > limit):
        raise ValueError("Invalid limited license MST scope")
    if policy["test"] and unlimited_scope and not bound_msts:
        raise ValueError("TEST license requires an MST")
    return {
        "version": 1,
        "plan": policy["raw"].upper(),
        "trial": bool(policy["test"]),
        "max_tax_codes": limit if limit is not None else (len(allowed) or None),
        "allowed_tax_codes": allowed,
        "date_from": TEST_DATE_FROM if policy["test"] else None,
        "date_to": TEST_DATE_TO if policy["test"] else None,
    }


def _extract_semver(value: str) -> Optional[tuple[int, int, int]]:
    match = SEMVER_RE.search(str(value or ""))
    return tuple(int(part) for part in match.groups()) if match else None


def _safe_update_url(value: str) -> str:
    value = str(value or "").strip()
    if not value or value.upper() == "U":
        return ""
    try:
        parsed = urlparse(value)
    except Exception:
        return ""
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "drive.google.com":
        return ""
    return value


def _update_info(vip_lines: Iterable[str], current_version: str = "") -> dict:
    # The first non-empty line is the single, explicit update channel. Keeping
    # it out of the license records prevents an accidental later line from
    # publishing an update.
    update_line = next((str(line).strip() for line in vip_lines if str(line).strip()), "")
    first_field = update_line.split("|", 1)[0].strip().lstrip("\ufeff").lower()
    if first_field != "update":
        update_line = ""
    if not update_line:
        return {"available": False, "url": "", "label": "", "latest_version": ""}
    parts = update_line.split("|")
    raw_target = parts[1].strip() if len(parts) > 1 else ""
    label = parts[2].strip() if len(parts) > 2 else ""
    url = _safe_update_url(raw_target)
    latest = _extract_semver(label)
    current = _extract_semver(current_version)
    available = bool(url)
    if available and latest is not None and current is not None:
        available = latest > current
    return {
        "available": available,
        "url": url if available else "",
        "label": label,
        "latest_version": ".".join(map(str, latest)) if latest else "",
        "current_version": ".".join(map(str, current)) if current else str(current_version or ""),
    }


def _finalize_v2_response(
    result: dict, *, key: str, line: str, paths: dict[str, Path],
    vip_lines: Iterable[str], mst: str = "", date_from: str = "",
    date_to: str = "", current_version: str = "",
) -> dict:
    """Attach license policy, MST quota, TEST date restriction, and update info."""
    result = dict(result)
    result["license_valid"] = bool(result.get("valid"))
    result["update"] = _update_info(vip_lines, current_version)
    try:
        policy = _policy_from_line(line)
        declared_msts, _unlimited_scope = _declared_msts(line)
        policy["declared_msts"] = declared_msts
    except ValueError:
        result.update({
            "valid": False,
            "authorized": False,
            "reason": "license_policy_invalid",
            "license_policy": "",
        })
        return result

    # Expose current quota even for a base key verification with no MST.
    mst_state = _mst_policy_state(paths, key, policy, mst if result.get("valid") else "")
    date_state = _test_date_state(policy, date_from if result.get("valid") else "", date_to if result.get("valid") else "")
    result.update(mst_state)
    result.update(date_state)

    try:
        result["entitlements"] = _entitlements_from_state(line, paths, key, policy)
    except ValueError:
        result.update({
            "valid": False, "license_valid": False, "authorized": False,
            "reason": "license_policy_invalid", "entitlements": None,
        })
        return result

    operation_has_mst = bool(mst)
    operation_has_date = bool(date_from or date_to)
    mst_ok = result.get("mst_authorized") is not False
    date_ok = result.get("date_authorized") is not False
    authorized = bool(result.get("license_valid")) and mst_ok and date_ok
    result["authorized"] = authorized
    if result.get("license_valid") and not authorized and (operation_has_mst or operation_has_date):
        result["valid"] = False
        reason = mst_state.get("authorization_reason") or date_state.get("authorization_reason") or "license_policy_denied"
        result["reason"] = reason
    return result


def _is_legacy_key_for_tool(tool: str, key: str) -> bool:
    """Return whether a legacy row is eligible for automatic V2 migration.

    GSOFT behavior intentionally matches the previous implementation exactly.
    MIA only enables the two legacy schemas that are currently evidenced:
    V1 key+29hex and the observed KEY+29hex+10-digit-phone format.
    """
    tool = str(tool or "").upper().strip()
    key = str(key or "").strip()

    if tool == "GSOFT":
        return key.startswith("KEY") and not key.startswith(NEW_KEY_PREFIX)

    if tool == "MIA":
        return bool(MIA_V1_RE.fullmatch(key) or MIA_V2_PHONE_RE.fullmatch(key))

    return False


def _ensure_legacy_seed(tool: str, paths: dict[str, Path]) -> None:
    """Merge eligible original rows into the per-tool migration allow-list.

    GSOFT keeps its previous legacy selection unchanged. MIA additionally
    supports its confirmed lowercase V1 key format. The source vip.txt is never
    rewritten here, so legacy desktop builds continue to use /verify-key.
    """
    legacy_lines = _read_lines(paths["legacy"])
    existing = {line.split("|", 1)[0].strip() for line in legacy_lines}
    changed = False
    sources = [paths["vip"], *paths.get("legacy_sources", [])]
    for line in [row for source in sources for row in _read_lines(source)]:
        key = line.split("|", 1)[0].strip()
        if not _is_legacy_key_for_tool(tool, key) or key in existing:
            continue
        legacy_lines.append(line)
        existing.add(key)
        changed = True
    if changed or not paths["legacy"].exists():
        _atomic_write(paths["legacy"], "\n".join(legacy_lines) + ("\n" if legacy_lines else ""))


def _append_vip_line(vip_path: Path, new_line: str) -> None:
    """Append/update a v2 row but never remove the legacy row.

    Keeping the original row means customers who have not upgraded their
    desktop app yet continue to validate through /verify-key unchanged.
    """
    lines = _read_lines(vip_path)
    new_key = new_line.split("|", 1)[0].strip()
    output = []
    replaced = False
    for line in lines:
        key = line.split("|", 1)[0].strip()
        if key == new_key:
            if not replaced:
                output.append(new_line)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(new_line)
    _atomic_write(vip_path, "\n".join(output) + "\n")


def _binding_payload(device_id: str, phone: str, hardware: Dict[str, str], source: str) -> dict:
    return {
        "device_id": device_id,
        "device_id_hash": hashlib.sha256(device_id.encode("utf-8")).hexdigest(),
        "phone": _clean_phone(phone),
        "hardware": dict(hardware),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
    }


def _response_for_binding(
    *,
    key: str,
    line: str,
    binding: dict,
    current_hw: Dict[str, str],
    migrated: bool = False,
    reason_ok: str = "ok",
) -> dict:
    expiry = _expiry_from_line(line)
    expired = _is_expired(expiry)
    ok, ratio, matches = _hardware_match(dict(binding.get("hardware") or {}), current_hw)
    reason = reason_ok if ok and not expired else ("expired" if expired else "hardware_mismatch_below_50_percent")
    return {
        "valid": ok and not expired,
        "key": key,
        "device_id": str(binding.get("device_id") or ""),
        "phone": str(binding.get("phone") or ""),
        "expires_at": expiry,
        "expired": expired,
        "migrated": migrated,
        "hardware_match": round(ratio, 3),
        "hardware_matches": matches,
        "hardware_profile": dict(binding.get("hardware") or {}),
        "reason": reason,
    }



def verify_key_v2(
    tool: str,
    *,
    key: str,
    device_id: str,
    phone: str,
    hardware: Dict[str, str],
    legacy_keys: List[str],
    mst: str = "",
    date_from: str = "",
    date_to: str = "",
    current_version: str = "",
) -> dict:
    tool = str(tool or "").upper().strip()
    if tool not in V2_TOOLS:
        raise ValueError("verify-key-v2 unsupported tool")
    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("Missing device_id")
    current_hw = _sanitize_hardware(hardware)
    supplied_key = str(key or "").strip()
    clean_phone = _clean_phone(phone)

    # Backward compatibility for the short-lived first GSOFT v2 pair.
    # MIA V2 intentionally requires a real phone so it uses the same stable
    # KEYV2-<digest>-<phone> contract as current Taxsoft/GSOFT.
    if not clean_phone:
        if tool != "GSOFT":
            raise ValueError("Missing phone")
        interim_key = _interim_v2_key(tool, device_id)
        if supplied_key != interim_key:
            raise ValueError("Missing phone")
        paths = _v2_paths(tool)
        with _LOCK, _process_lock(paths["lock"]):
            vip_lines = _read_lines(paths["vip"])
            active_line = _find_key_line(vip_lines, interim_key)
            if not active_line:
                return {
                    "valid": False,
                    "key": interim_key,
                    "device_id": device_id,
                    "expired": False,
                    "migrated": False,
                    "hardware_match": 1.0,
                    "reason": "key_not_activated",
                }
            bindings = _load_json(paths["bindings"])
            binding = dict(bindings.get(interim_key) or {})
            if not binding:
                binding = _binding_payload(device_id, "", current_hw, "interim_v2_activation")
                bindings[interim_key] = binding
                _atomic_json(paths["bindings"], bindings)
            return _finalize_v2_response(
                _response_for_binding(
                    key=interim_key,
                    line=active_line,
                    binding=binding,
                    current_hw=current_hw,
                ),
                key=interim_key, line=active_line, paths=paths, vip_lines=vip_lines,
                mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
            )

    expected_key = _new_key(tool, device_id, clean_phone)
    if supplied_key != expected_key:
        raise ValueError("Invalid v2 key for device_id/phone")

    paths = _v2_paths(tool)
    with _LOCK, _process_lock(paths["lock"]):
        _ensure_legacy_seed(tool, paths)
        vip_lines = _read_lines(paths["vip"])
        bindings = _load_json(paths["bindings"])
        migrations = _load_json(paths["migrations"])

        # 1) Normal verification. A manually-added new key is bound on first use.
        active_line = _find_key_line(vip_lines, expected_key)
        if active_line:
            binding = dict(bindings.get(expected_key) or {})
            if not binding:
                binding = _binding_payload(device_id, clean_phone, current_hw, "manual_activation")
                bindings[expected_key] = binding
                _atomic_json(paths["bindings"], bindings)
            return _finalize_v2_response(
                _response_for_binding(
                    key=expected_key,
                    line=active_line,
                    binding=binding,
                    current_hw=current_hw,
                ),
                key=expected_key, line=active_line, paths=paths, vip_lines=vip_lines,
                mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
            )

        # 2) Upgrade the interim KEY2 format only for GSOFT.
        # This block is intentionally unchanged in behavior for Taxsoft.
        if tool == "GSOFT":
            interim_key = _interim_v2_key(tool, device_id)
            interim_line = _find_key_line(vip_lines, interim_key)
            interim_binding = dict(bindings.get(interim_key) or {})
            if interim_line and interim_binding:
                ok, ratio, matches = _hardware_match(dict(interim_binding.get("hardware") or {}), current_hw)
                if not ok:
                    return {
                        "valid": False,
                        "key": expected_key,
                        "device_id": device_id,
                        "phone": clean_phone,
                        "expired": _is_expired(_expiry_from_line(interim_line)),
                        "migrated": False,
                        "hardware_match": round(ratio, 3),
                        "hardware_matches": matches,
                        "reason": "hardware_mismatch_below_50_percent",
                    }
                parts = interim_line.split("|")
                parts[0] = expected_key
                new_line = "|".join(parts)
                _append_vip_line(paths["vip"], new_line)
                binding = _binding_payload(device_id, clean_phone, current_hw, "interim_v2_upgrade")
                bindings[expected_key] = binding
                _atomic_json(paths["bindings"], bindings)
                return _finalize_v2_response(
                    _response_for_binding(
                        key=expected_key,
                        line=new_line,
                        binding=binding,
                        current_hw=current_hw,
                        migrated=True,
                        reason_ok="interim_v2_upgraded",
                    ),
                    key=expected_key, line=new_line, paths=paths, vip_lines=_read_lines(paths["vip"]),
                    mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
                )

        # 3) If the local profile was lost/recreated, recover the already-bound
        # canonical KEYV2 using phone + >=50% hardware instead of changing key.
        recovery_candidates = []
        for bound_key, raw_binding in bindings.items():
            if not str(bound_key).startswith(NEW_KEY_PREFIX):
                continue
            binding = dict(raw_binding or {})
            if _clean_phone(str(binding.get("phone") or "")) != clean_phone:
                continue
            line = _find_key_line(vip_lines, str(bound_key))
            if not line:
                continue
            ok, ratio, matches = _hardware_match(dict(binding.get("hardware") or {}), current_hw)
            if ok:
                recovery_candidates.append((ratio, matches, str(bound_key), line, binding))
        if recovery_candidates:
            recovery_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            if (len(recovery_candidates) > 1
                    and recovery_candidates[0][:2] == recovery_candidates[1][:2]):
                return {
                    "valid": False, "key": expected_key, "device_id": device_id,
                    "phone": clean_phone, "expired": False, "migrated": False,
                    "reason": "recovery_ambiguous",
                }
            _, _, canonical_key, canonical_line, canonical_binding = recovery_candidates[0]
            result = _finalize_v2_response(
                _response_for_binding(
                    key=canonical_key,
                    line=canonical_line,
                    binding=canonical_binding,
                    current_hw=current_hw,
                    reason_ok="recovered_existing_device",
                ),
                key=canonical_key, line=canonical_line, paths=paths, vip_lines=vip_lines,
                mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
            )
            result["recovered"] = True
            return result

        legacy_lines = _read_lines(paths["legacy"])
        legacy_map = {}
        for line in legacy_lines:
            legacy_key = line.split("|", 1)[0].strip()
            if _is_legacy_key_for_tool(tool, legacy_key):
                legacy_map[legacy_key] = line

        # 4) Exact legacy reconstruction from phone.txt + legacy hardware source.
        candidates = [
            (legacy_key, legacy_map[legacy_key])
            for legacy_key in [str(v or "").strip() for v in (legacy_keys or [])[:32] if v]
            if legacy_key in legacy_map
        ]
        # Prefer an unexpired phone-bound renewal over an older expired disk key.
        candidates.sort(key=lambda item: (
            _is_expired(_expiry_from_line(item[1])),
            not MIA_V2_PHONE_RE.fullmatch(item[0]) if tool == "MIA" else False,
        ))
        selected_legacy_key, selected_legacy_line = candidates[0] if candidates else ("", "")

        # Do not migrate by phone alone. The old key must be reconstructed
        # exactly from phone.txt + the legacy device source. This prevents a
        # caller who only knows a customer's phone number from claiming the
        # legacy license on another machine.

        if selected_legacy_line:
            previous = dict(migrations.get(selected_legacy_key) or {})
            if previous:
                previous_key = str(previous.get("new_key") or "")
                previous_line = _find_key_line(vip_lines, previous_key)
                previous_binding = dict(bindings.get(previous_key) or {})
                if previous_line and previous_binding:
                    return _finalize_v2_response(
                        _response_for_binding(
                            key=previous_key,
                            line=previous_line,
                            binding=previous_binding,
                            current_hw=current_hw,
                            migrated=True,
                            reason_ok="legacy_already_migrated",
                        ),
                        key=previous_key, line=previous_line, paths=paths, vip_lines=vip_lines,
                        mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
                    )
                return {
                    "valid": False,
                    "key": expected_key,
                    "device_id": device_id,
                    "phone": clean_phone,
                    "expired": False,
                    "migrated": False,
                    "hardware_match": 0.0,
                    "reason": "legacy_migration_record_incomplete",
                }

            expiry = _expiry_from_line(selected_legacy_line)
            if _is_expired(expiry):
                return {
                    "valid": False,
                    "key": expected_key,
                    "device_id": device_id,
                    "phone": clean_phone,
                    "expires_at": expiry,
                    "expired": True,
                    "migrated": False,
                    "hardware_match": 1.0,
                    "reason": "legacy_key_expired",
                }

            # Reject malformed/over-broad TEST metadata before writing a new key.
            try:
                candidate_policy = _policy_from_line(selected_legacy_line)
                declared_msts, unlimited_scope = _declared_msts(selected_legacy_line)
                if candidate_policy["limit"] is not None and (
                        not declared_msts or len(declared_msts) > candidate_policy["limit"]
                ):
                    raise ValueError("Invalid limited license MST scope")
                if candidate_policy["test"] and unlimited_scope:
                    raise ValueError("TEST license requires an MST")
            except ValueError:
                return {
                    "valid": False, "key": expected_key, "device_id": device_id,
                    "phone": clean_phone, "expired": False, "migrated": False,
                    "reason": "license_policy_invalid",
                }

            parts = selected_legacy_line.split("|")
            parts[0] = expected_key
            new_line = "|".join(parts)
            # Intentionally KEEP the old row for older desktop builds.
            _append_vip_line(paths["vip"], new_line)

            binding = _binding_payload(device_id, clean_phone, current_hw, "legacy_migration")
            bindings[expected_key] = binding
            migrations[selected_legacy_key] = {
                "new_key": expected_key,
                "phone": clean_phone,
                "migrated_at": datetime.now().isoformat(timespec="seconds"),
            }
            _atomic_json(paths["bindings"], bindings)
            _atomic_json(paths["migrations"], migrations)

            return _finalize_v2_response(
                _response_for_binding(
                    key=expected_key,
                    line=new_line,
                    binding=binding,
                    current_hw=current_hw,
                    migrated=True,
                    reason_ok="legacy_migrated",
                ),
                key=expected_key, line=new_line, paths=paths, vip_lines=_read_lines(paths["vip"]),
                mst=mst, date_from=date_from, date_to=date_to, current_version=current_version,
            )

        # 5) Truly new customer: show a stable KEYV2 and wait for manual activation.
        return {
            "valid": False,
            "key": expected_key,
            "device_id": device_id,
            "phone": clean_phone,
            "expired": False,
            "migrated": False,
            "hardware_match": 1.0,
            "reason": "key_not_activated",
        }
