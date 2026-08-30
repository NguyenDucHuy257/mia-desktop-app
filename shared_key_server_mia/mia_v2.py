"""MIA-only extension for the existing /opt/keys_app shared key server.

This module does not create an HTTP app, listener, service or database. The
existing auth.py dispatches tool=MIA here while its GSOFT branch stays intact.
"""

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

TOOL = "MIA"
NEW_KEY_PREFIX = "MIAV2-"
HARDWARE_FIELDS = {
    "system_uuid", "bios_serial", "baseboard_serial",
    "machine_guid", "cpu_id", "disk_serial",
}
MIN_HARDWARE_FIELDS = 3
MIN_MATCH_RATIO = 0.50
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PHONE_RE = re.compile(r"^0[0-9]{9}$")
V1_RE = re.compile(r"^key[0-9a-f]{29}$")
OBSERVED_V2_RE = re.compile(r"^KEY[0-9a-f]{29}0[0-9]{9}$")
_LOCK = threading.RLock()


@contextmanager
def _process_lock(lock_path: Path):
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


def _paths(base_dir: str | Path) -> dict[str, Path]:
    folder = Path(base_dir).resolve() / TOOL
    return {
        "vip": folder / "vip.txt",
        "legacy": folder / "legacy_vip.txt",
        "bindings": folder / "device_bindings.json",
        "migrations": folder / "legacy_migrations.json",
        "lock": folder / ".license_v2.lock",
    }


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.rstrip("\r\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, value: dict) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: Path) -> dict:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _find_key_line(lines: Iterable[str], key: str) -> Optional[str]:
    return next((line for line in lines if line.split("|", 1)[0].strip() == key), None)


def _expiry(line: str) -> str:
    parts = line.split("|")
    return parts[2].strip() if len(parts) > 2 else ""


def _expired(value: str, now: datetime) -> bool:
    if not value:
        return False
    try:
        return datetime.strptime(value, "%d/%m/%Y").date() < now.date()
    except ValueError:
        return False


def _phone(value: str, *, required: bool = False) -> str:
    normalized = re.sub(r"[\s.()-]+", "", str(value or "").strip())
    if not normalized and not required:
        return ""
    if not PHONE_RE.fullmatch(normalized) or normalized == "0000000000":
        raise ValueError("Invalid MIA phone")
    return normalized


def _hardware(value: Dict[str, str]) -> Dict[str, str]:
    clean = {
        name: str(signal or "").strip().lower()
        for name, signal in dict(value or {}).items()
        if name in HARDWARE_FIELDS and HASH_RE.fullmatch(str(signal or "").strip().lower())
    }
    if len(clean) < MIN_HARDWARE_FIELDS:
        raise ValueError(f"Not enough hardware signals: need at least {MIN_HARDWARE_FIELDS}/{len(HARDWARE_FIELDS)}")
    return clean


def _hardware_match(saved: Dict[str, str], current: Dict[str, str]) -> tuple[bool, float, int]:
    baseline = {name: value for name, value in dict(saved or {}).items() if name in HARDWARE_FIELDS and HASH_RE.fullmatch(str(value))}
    if len(baseline) < MIN_HARDWARE_FIELDS:
        return False, 0.0, 0
    matches = sum(1 for name, value in baseline.items() if current.get(name) == value)
    ratio = matches / len(baseline)
    return matches >= MIN_HARDWARE_FIELDS and ratio >= MIN_MATCH_RATIO, ratio, matches


def _key(device_id: str) -> str:
    digest = hashlib.sha256(f"{TOOL}|{device_id}".encode("utf-8")).hexdigest()
    return f"{NEW_KEY_PREFIX}{digest[:32]}"


def _ensure_legacy_seed(paths: dict[str, Path]) -> None:
    legacy = _read_lines(paths["legacy"])
    existing = {line.split("|", 1)[0].strip() for line in legacy}
    changed = False
    for line in _read_lines(paths["vip"]):
        key = line.split("|", 1)[0].strip()
        if key.startswith(NEW_KEY_PREFIX) or key in existing:
            continue
        legacy.append(line)
        existing.add(key)
        changed = True
    if changed or not paths["legacy"].exists():
        _atomic_write(paths["legacy"], "\n".join(legacy) + ("\n" if legacy else ""))


def _append_vip_line(path: Path, new_line: str) -> None:
    lines = _read_lines(path)
    new_key = new_line.split("|", 1)[0].strip()
    output = [line for line in lines if line.split("|", 1)[0].strip() != new_key]
    output.append(new_line)
    _atomic_write(path, "\n".join(output) + "\n")


def _binding(device_id: str, phone: str, hardware: Dict[str, str], source: str, now: datetime) -> dict:
    return {
        "device_id": device_id,
        "phone": phone,
        "phone_status": "verified" if phone else "pending",
        "hardware": dict(hardware),
        "source": source,
        "created_at": now.isoformat(timespec="seconds"),
    }


def _response(key: str, line: str, binding: dict, hardware: Dict[str, str], now: datetime, *, migrated=False, recovered=False, reason="ok") -> dict:
    expiry = _expiry(line)
    is_expired = _expired(expiry, now)
    ok, ratio, matches = _hardware_match(dict(binding.get("hardware") or {}), hardware)
    final_reason = "expired" if is_expired else reason if ok else "hardware_mismatch_below_50_percent"
    return {
        "valid": ok and not is_expired,
        "key": key,
        "device_id": str(binding.get("device_id") or ""),
        "phone": str(binding.get("phone") or ""),
        "phone_status": str(binding.get("phone_status") or ("verified" if binding.get("phone") else "pending")),
        "hardware_profile": dict(binding.get("hardware") or {}),
        "expires_at": expiry,
        "expired": is_expired,
        "migrated": migrated,
        "recovered": recovered,
        "hardware_match": round(ratio, 3),
        "hardware_matches": matches,
        "reason": final_reason,
    }


def verify_mia_key_v2(
    base_dir: str | Path,
    *,
    key: str,
    device_id: str,
    phone: str,
    hardware: Dict[str, str],
    legacy_keys: List[str],
    now: datetime | None = None,
) -> dict:
    """Verify/migrate MIA state inside BASE_DIR/MIA only."""
    now = now or datetime.now()
    device_id = str(device_id or "").strip()
    if not device_id or len(device_id) > 128:
        raise ValueError("Missing or invalid device_id")
    current_hardware = _hardware(hardware)
    clean_phone = _phone(phone)
    expected_key = _key(device_id)
    if str(key or "").strip() != expected_key:
        raise ValueError("Invalid MIA v2 key for device_id")
    paths = _paths(base_dir)

    with _LOCK, _process_lock(paths["lock"]):
        _ensure_legacy_seed(paths)
        vip_lines = _read_lines(paths["vip"])
        bindings = _load_json(paths["bindings"])
        migrations = _load_json(paths["migrations"])

        # Normal verification; a manually-added MIAV2 key binds on first use.
        active_line = _find_key_line(vip_lines, expected_key)
        if active_line:
            saved = dict(bindings.get(expected_key) or {})
            if not saved:
                if not clean_phone:
                    return {"valid": False, "key": expected_key, "device_id": device_id, "phone": "", "expired": False, "migrated": False, "reason": "phone_required"}
                saved = _binding(device_id, clean_phone, current_hardware, "manual_activation", now)
                bindings[expected_key] = saved
                _atomic_json(paths["bindings"], bindings)
            elif clean_phone and not saved.get("phone"):
                saved["phone"] = clean_phone
                saved["phone_status"] = "verified"
                bindings[expected_key] = saved
                _atomic_json(paths["bindings"], bindings)
            return _response(expected_key, active_line, saved, current_hardware, now)

        # Lost local profile: recover canonical device/key using real phone and
        # >=3 matching fields with a >=50% ratio.
        recovery = []
        if clean_phone:
            for canonical_key, raw in bindings.items():
                if not str(canonical_key).startswith(NEW_KEY_PREFIX):
                    continue
                saved = dict(raw or {})
                if _phone(str(saved.get("phone") or "")) != clean_phone:
                    continue
                line = _find_key_line(vip_lines, str(canonical_key))
                if not line:
                    continue
                ok, ratio, matches = _hardware_match(dict(saved.get("hardware") or {}), current_hardware)
                if ok:
                    recovery.append((ratio, matches, str(canonical_key), line, saved))
        recovery.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if recovery:
            if len(recovery) > 1 and recovery[0][:2] == recovery[1][:2]:
                return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "expired": False, "migrated": False, "reason": "recovery_ambiguous"}
            _, _, canonical_key, line, saved = recovery[0]
            return _response(canonical_key, line, saved, current_hardware, now, recovered=True, reason="recovered_existing_device")

        legacy_map = {
            line.split("|", 1)[0].strip(): line
            for line in _read_lines(paths["legacy"])
            if V1_RE.fullmatch(line.split("|", 1)[0].strip())
            or OBSERVED_V2_RE.fullmatch(line.split("|", 1)[0].strip())
        }
        selected_key = next((candidate for candidate in [str(value or "").strip() for value in list(legacy_keys or [])[:32]] if candidate in legacy_map), "")
        if selected_key:
            legacy_line = legacy_map[selected_key]
            previous = dict(migrations.get(selected_key) or {})
            if previous:
                previous_key = str(previous.get("new_key") or "")
                previous_line = _find_key_line(vip_lines, previous_key)
                previous_binding = dict(bindings.get(previous_key) or {})
                if previous_line and previous_binding:
                    return _response(previous_key, previous_line, previous_binding, current_hardware, now, migrated=True, reason="legacy_already_migrated")
                return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "expired": False, "migrated": False, "reason": "legacy_migration_record_incomplete"}
            expiry = _expiry(legacy_line)
            if _expired(expiry, now):
                return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "phone_status": "verified" if clean_phone else "pending", "expires_at": expiry, "expired": True, "migrated": False, "reason": "legacy_key_expired"}
            parts = legacy_line.split("|")
            parts[0] = expected_key
            new_line = "|".join(parts)
            _append_vip_line(paths["vip"], new_line)
            saved = _binding(device_id, clean_phone, current_hardware, "legacy_migration", now)
            bindings[expected_key] = saved
            migrations[selected_key] = {
                "new_key": expected_key,
                "device_id": device_id,
                "phone_status": saved["phone_status"],
                "migrated_at": now.isoformat(timespec="seconds"),
            }
            _atomic_json(paths["bindings"], bindings)
            _atomic_json(paths["migrations"], migrations)
            return _response(expected_key, new_line, saved, current_hardware, now, migrated=True, reason="legacy_migrated")

        if not clean_phone:
            return {"valid": False, "key": expected_key, "device_id": device_id, "phone": "", "phone_status": "pending", "expired": False, "migrated": False, "reason": "phone_required"}
        return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "phone_status": "verified", "expired": False, "migrated": False, "reason": "key_not_activated"}


__all__ = ["verify_mia_key_v2"]
