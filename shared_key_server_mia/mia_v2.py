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
NEW_KEY_PREFIX = "KEYV2-"
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
    root = Path(base_dir).resolve()
    folder = root / TOOL
    legacy_registries = sorted(
        (
            candidate / "vip.txt"
            for candidate in root.iterdir()
            if candidate.is_dir() and re.fullmatch(r"MIA\d+", candidate.name)
        ),
        key=lambda candidate: int(candidate.parent.name[3:]),
    ) if root.exists() else []
    return {
        "root": root,
        "vip": folder / "vip.txt",
        "legacy": folder / "legacy_vip.txt",
        # Historical MIA clients used MIA2, MIA3, ... namespaces. They are
        # migration registries for the same product, never other tool folders.
        "legacy_registries": legacy_registries,
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
    if not path.exists():
        return {}
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Corrupt MIA license state: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Corrupt MIA license state: {path.name}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Corrupt MIA license state: {path.name}")
    return value


def _find_key_line(lines: Iterable[str], key: str) -> Optional[str]:
    return next((line for line in lines if line.split("|", 1)[0].strip() == key), None)


def _line_key(line: str) -> str:
    return line.split("|", 1)[0].strip()


def _canonical_line(key: str, source_line: str) -> str:
    return key + "|" + source_line.split("|", 1)[1]


def _replace_migrated_key(
    path: Path,
    legacy_key: str,
    canonical_key: str,
    canonical_line: str,
    *,
    ensure_canonical: bool,
) -> bool:
    """Replace one migrated legacy key and collapse canonical duplicates.

    The first canonical row is authoritative. If none exists, the migrated
    row replaces the first legacy row; MIA/vip.txt may additionally append it.
    """
    lines = _read_lines(path)
    first_canonical = _find_key_line(lines, canonical_key)
    selected = canonical_line
    output: list[str] = []
    inserted = False
    touched = False
    for line in lines:
        key = _line_key(line)
        if key == canonical_key:
            if not inserted:
                output.append(selected)
                inserted = True
            else:
                touched = True
            if line != selected:
                touched = True
            continue
        if key == legacy_key:
            touched = True
            if not inserted and first_canonical is None:
                output.append(selected)
                inserted = True
            continue
        output.append(line)
    if ensure_canonical and not inserted:
        output.append(selected)
        inserted = True
        touched = True
    if touched:
        _atomic_write(path, "\n".join(output) + ("\n" if output else ""))
    return touched


def _expiry(line: str) -> str:
    parts = line.split("|")
    if len(parts) > 1 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", parts[1].strip()):
        return parts[1].strip()  # Older key|expiry|l|phone|MST layout.
    return parts[2].strip() if len(parts) > 2 else ""


def _entitlements(line: str) -> dict:
    parts = [part.strip() for part in line.split("|")]
    plan = parts[1].upper() if len(parts) > 1 else ""
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", plan):
        plan = "V"
    trial = re.fullmatch(r"TEST([1-9]\d*)?", plan)
    paid_limited = re.fullmatch(r"VIP([1-9]\d*)", plan)
    if not trial and not paid_limited and plan not in {"V", "VIP"}:
        raise ValueError("license_policy_invalid")
    scope = parts[4] if len(parts) > 4 else ""
    # Only parse the scope field, never contact numbers or trailing notes.
    # Preserve branch IDs; a parent MST does not authorize all its branches.
    ids = list(dict.fromkeys(re.findall(r"(?<![\w-])(?:\d{12}|\d{10})(?:-(?:U)?\d{3})?(?![\w-])", scope)))
    unlimited = scope.lower() == "o" or not scope
    if not unlimited and not ids:
        raise ValueError("license_policy_invalid")
    maximum = int(trial.group(1) or 1) if trial else int(paid_limited.group(1)) if paid_limited else None
    if trial and (not ids or len(ids) > maximum):
        raise ValueError("license_policy_invalid")
    if paid_limited and (not ids or len(ids) > maximum):
        raise ValueError("license_policy_invalid")
    return {
        "version": 1, "plan": plan, "trial": bool(trial),
        "max_tax_codes": maximum if trial or paid_limited else len(ids) if ids else None,
        "allowed_tax_codes": ids,
        "date_from": "2026-08-01" if trial else None,
        "date_to": "2026-08-31" if trial else None,
    }


def _expired(value: str, now: datetime) -> bool:
    if not value:
        return False
    try:
        return datetime.strptime(value, "%d/%m/%Y").date() < now.date()
    except ValueError:
        return True


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


def _key(device_id: str, phone: str) -> str:
    clean_phone = _phone(phone, required=True)
    digest = hashlib.sha256(f"{TOOL}|{device_id}".encode("utf-8")).hexdigest()
    return f"{NEW_KEY_PREFIX}{digest[:32]}-{clean_phone}"


def _ensure_legacy_seed(paths: dict[str, Path]) -> None:
    legacy = _read_lines(paths["legacy"])
    existing = {line.split("|", 1)[0].strip() for line in legacy}
    changed = False
    source_lines = [*_read_lines(paths["vip"])]
    for registry in paths["legacy_registries"]:
        source_lines.extend(_read_lines(registry))
    for line in source_lines:
        key = line.split("|", 1)[0].strip()
        if not (V1_RE.fullmatch(key) or OBSERVED_V2_RE.fullmatch(key)) or key in existing:
            continue
        legacy.append(line)
        existing.add(key)
        changed = True
    if changed or not paths["legacy"].exists():
        _atomic_write(paths["legacy"], "\n".join(legacy) + ("\n" if legacy else ""))


def _reconcile_migrations(
    paths: dict[str, Path], migrations: dict, bindings: dict,
    *, only_legacy_keys: Optional[Iterable[str]] = None,
) -> bool:
    """Converge registries with one read and at most one write per file."""
    targets = (
        {str(key or "").strip() for key in only_legacy_keys if str(key or "").strip()}
        if only_legacy_keys is not None else None
    )
    migration_changed = False
    registries = [paths["vip"], paths["legacy"], *paths["legacy_registries"]]
    registry_lines = {registry: _read_lines(registry) for registry in registries}
    first_by_key: dict[str, str] = {}
    for registry in registries:
        for line in registry_lines[registry]:
            first_by_key.setdefault(_line_key(line), line)

    canonical_lines: dict[str, str] = {}
    legacy_replacements: dict[str, str] = {}
    for legacy_key, raw_migration in list(migrations.items()):
        if targets is not None and legacy_key not in targets:
            continue
        if not isinstance(raw_migration, dict):
            continue
        migration = dict(raw_migration)
        canonical_key = str(migration.get("new_key") or "").strip()
        binding = bindings.get(canonical_key)
        if not canonical_key.startswith(NEW_KEY_PREFIX) or not isinstance(binding, dict) or not binding:
            continue

        selected = canonical_lines.get(canonical_key) or first_by_key.get(canonical_key)
        if not selected:
            stored = str(migration.get("canonical_line") or "").strip()
            if "|" in stored and _line_key(stored) == canonical_key:
                selected = stored
        if not selected:
            legacy_line = first_by_key.get(legacy_key)
            if legacy_line and "|" in legacy_line:
                selected = _canonical_line(canonical_key, legacy_line)
        if not selected:
            continue

        selected = canonical_lines.setdefault(canonical_key, selected)
        legacy_replacements[legacy_key] = canonical_key

        if migration.get("canonical_line") != selected:
            migration["canonical_line"] = selected
            migrations[legacy_key] = migration
            migration_changed = True

    changed = False
    canonical_order = list(canonical_lines)
    for registry in registries:
        lines = registry_lines[registry]
        present_canonical = {
            _line_key(line) for line in lines
            if _line_key(line) in canonical_lines
        }
        output: list[str] = []
        emitted: set[str] = set()
        for line in lines:
            key = _line_key(line)
            if key in canonical_lines:
                if key not in emitted:
                    output.append(canonical_lines[key])
                    emitted.add(key)
                continue
            canonical_key = legacy_replacements.get(key)
            if canonical_key:
                if canonical_key not in present_canonical and canonical_key not in emitted:
                    output.append(canonical_lines[canonical_key])
                    emitted.add(canonical_key)
                continue
            output.append(line)

        if registry == paths["vip"]:
            for canonical_key in canonical_order:
                if canonical_key not in emitted:
                    output.append(canonical_lines[canonical_key])
                    emitted.add(canonical_key)

        if output != lines:
            _atomic_write(registry, "\n".join(output) + ("\n" if output else ""))
            changed = True
    if migration_changed:
        _atomic_json(paths["migrations"], migrations)
    return changed or migration_changed


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


def _response(key: str, line: str, binding: dict, hardware: Dict[str, str], now: datetime, *, migrated=False, recovered=False) -> dict:
    expiry = _expiry(line)
    is_expired = _expired(expiry, now)
    ok, ratio, matches = _hardware_match(dict(binding.get("hardware") or {}), hardware)
    # Successful verification has one canonical authorization reason. Migration
    # and recovery remain explicit boolean metadata, not alternate allow reasons.
    final_reason = "expired" if is_expired else "ok" if ok else "hardware_mismatch_below_50_percent"
    try:
        entitlements = _entitlements(line)
    except ValueError:
        entitlements = None
        final_reason = "license_policy_invalid"
    return {
        "valid": ok and not is_expired and entitlements is not None,
        "entitlements": entitlements,
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
    clean_phone = _phone(phone, required=True)
    expected_key = _key(device_id, clean_phone)
    if str(key or "").strip() != expected_key:
        raise ValueError("Invalid MIA v2 key for device_id")
    paths = _paths(base_dir)
    requested_legacy_keys = [
        str(value or "").strip()
        for value in list(legacy_keys or [])[:32]
        if str(value or "").strip()
    ]

    with _LOCK, _process_lock(paths["lock"]):
        _ensure_legacy_seed(paths)
        vip_lines = _read_lines(paths["vip"])
        bindings = _load_json(paths["bindings"])
        migrations = _load_json(paths["migrations"])
        reconcile_keys = set(requested_legacy_keys)
        reconcile_keys.update(
            legacy_key
            for legacy_key, migration in migrations.items()
            if isinstance(migration, dict)
            and str(migration.get("new_key") or "").strip() == expected_key
        )
        # Repair only migrations evidenced by this device. This keeps the
        # request cost bounded while preserving row replacement/self-healing.
        if reconcile_keys and _reconcile_migrations(
                paths, migrations, bindings,
                only_legacy_keys=reconcile_keys,
        ):
            vip_lines = _read_lines(paths["vip"])

        # Normal verification; a manually-added KEYV2 key binds on first use.
        active_line = _find_key_line(vip_lines, expected_key)
        if active_line:
            if expected_key not in bindings:
                saved = _binding(device_id, clean_phone, current_hardware, "manual_activation", now)
                bindings[expected_key] = saved
                _atomic_json(paths["bindings"], bindings)
            else:
                raw_saved = bindings[expected_key]
                if not isinstance(raw_saved, dict):
                    raise RuntimeError("Corrupt MIA license binding")
                saved = dict(raw_saved)
                try:
                    saved_hardware = _hardware(saved.get("hardware") or {})
                except (TypeError, ValueError) as error:
                    raise RuntimeError("Corrupt MIA license binding") from error
                if not str(saved.get("device_id") or "").strip() or len(saved_hardware) < MIN_HARDWARE_FIELDS:
                    raise RuntimeError("Corrupt MIA license binding")
            if clean_phone and not saved.get("phone"):
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
            return _response(canonical_key, line, saved, current_hardware, now, recovered=True)

        legacy_lines = [*_read_lines(paths["legacy"]), *vip_lines]
        for registry in paths["legacy_registries"]:
            legacy_lines.extend(_read_lines(registry))
        legacy_map = {
            line.split("|", 1)[0].strip(): line
            for line in legacy_lines
            if V1_RE.fullmatch(line.split("|", 1)[0].strip())
            or OBSERVED_V2_RE.fullmatch(line.split("|", 1)[0].strip())
        }
        candidates = [
            candidate
            for candidate in requested_legacy_keys
            if candidate in legacy_map or candidate in migrations
        ]
        # An old expired V1 candidate must not hide a renewed phone-bound key.
        # A completed migration is preferred over any remaining legacy record.
        # Never combine grants from separate records.
        candidates.sort(key=lambda candidate: (
            candidate not in migrations,
            _expired(_expiry(legacy_map[candidate]), now) if candidate in legacy_map else False,
            not candidate.startswith("KEY"),
        ))
        selected_key = candidates[0] if candidates else ""
        if selected_key:
            previous = dict(migrations.get(selected_key) or {})
            if previous:
                previous_key = str(previous.get("new_key") or "")
                previous_line = _find_key_line(vip_lines, previous_key)
                previous_binding = dict(bindings.get(previous_key) or {})
                if previous_line and previous_binding:
                    return _response(previous_key, previous_line, previous_binding, current_hardware, now, migrated=True)
                return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "expired": False, "migrated": False, "reason": "legacy_migration_record_incomplete"}
            legacy_line = legacy_map[selected_key]
            expiry = _expiry(legacy_line)
            if _expired(expiry, now):
                return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "phone_status": "verified" if clean_phone else "pending", "expires_at": expiry, "expired": True, "migrated": False, "reason": "legacy_key_expired"}
            parts = legacy_line.split("|")
            try:
                _entitlements(legacy_line)
            except ValueError:
                return {"valid": False, "expired": False, "reason": "license_policy_invalid"}
            parts[0] = expected_key
            new_line = "|".join(parts)
            _append_vip_line(paths["vip"], new_line)
            saved = _binding(device_id, clean_phone, current_hardware, "legacy_migration", now)
            bindings[expected_key] = saved
            migrations[selected_key] = {
                "new_key": expected_key,
                "canonical_line": new_line,
                "device_id": device_id,
                "phone_status": saved["phone_status"],
                "migrated_at": now.isoformat(timespec="seconds"),
            }
            _atomic_json(paths["bindings"], bindings)
            _atomic_json(paths["migrations"], migrations)
            _reconcile_migrations(
                paths, migrations, bindings,
                only_legacy_keys=[selected_key],
            )
            return _response(expected_key, new_line, saved, current_hardware, now, migrated=True)

        return {"valid": False, "key": expected_key, "device_id": device_id, "phone": clean_phone, "phone_status": "verified", "expired": False, "migrated": False, "reason": "key_not_activated"}


__all__ = ["verify_mia_key_v2"]
