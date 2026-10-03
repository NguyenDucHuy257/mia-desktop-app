from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


PLAN_RE = re.compile(r"^(VIP|TEST)([1-9][0-9]*)?$", re.IGNORECASE)
MST_RE = re.compile(r"^(?:[0-9]{10}(?:-[0-9]{3})?|[0-9]{12})$")
KEYV2_RE = re.compile(r"^KEYV2-[0-9a-f]{32}-0[0-9]{9}$", re.IGNORECASE)


def normalize_mst(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or ""))
    if value.isdigit() and len(value) == 13:
        value = value[:10] + "-" + value[10:]
    if not MST_RE.fullmatch(value):
        raise ValueError(f"MST không hợp lệ: {value}")
    return value


def normalize_plan(value: str) -> tuple[str, int | None, bool]:
    value = str(value or "").strip().upper()
    match = PLAN_RE.fullmatch(value)
    if not match:
        raise ValueError("Gói phải là VIP, VIP<N>, TEST hoặc TEST<N>")
    kind, suffix = match.groups()
    if kind == "VIP" and not suffix:
        return "VIP", None, False
    limit = int(suffix or 1)
    return (kind if not suffix else f"{kind}{limit}"), limit, kind == "TEST"


def build_line(key: str, plan: str, expiry: str, contact: str, msts: list[str]) -> str:
    plan, limit, is_test = normalize_plan(plan)
    datetime.strptime(expiry, "%d/%m/%Y")
    contact = str(contact or "").strip()
    if "|" in contact or "\n" in contact or "\r" in contact:
        raise ValueError("Liên hệ chứa ký tự không hợp lệ")
    normalized = []
    for item in msts:
        mst = normalize_mst(item)
        if mst not in normalized:
            normalized.append(mst)
    if limit is not None and len(normalized) > limit:
        raise ValueError(f"{plan} chỉ cho phép tối đa {limit} MST khai báo trước")
    # Không khai MST (scope "o") trên gói giới hạn = quota động: server tự ghi
    # N MST đầu tiên khách dùng vào mst_bindings.txt, MST thứ N+1 bị chặn
    # (mst_limit_reached). Khai danh sách cụ thể = khóa cứng phạm vi.
    scope = ",".join(normalized) if normalized else "o"
    return "|".join([key, plan, expiry, contact, scope])


@contextmanager
def process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + ("\n" if lines else ""))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def migration_targets(folder: Path, key: str) -> set[str]:
    targets = {key}
    path = folder / "legacy_migrations.json"
    if not path.exists():
        return targets
    payload = json.loads(path.read_text(encoding="utf-8"))
    for legacy_key, raw in dict(payload).items():
        canonical = str(dict(raw or {}).get("new_key") or "").strip()
        if legacy_key == key and canonical:
            targets.add(canonical)
        if canonical == key:
            targets.add(str(legacy_key))
    return targets


def update_registry(folder: Path, line: str, *, existing_only: bool) -> dict:
    requested_key = line.split("|", 1)[0]
    targets = migration_targets(folder, requested_key)
    changed, found = [], set()
    for name in ("vip.txt", "legacy_vip.txt"):
        path = folder / name
        lines = read_lines(path)
        output = []
        file_changed = False
        for old in lines:
            old_key = old.split("|", 1)[0].strip()
            if old_key in targets:
                replacement = old_key + "|" + line.split("|", 1)[1]
                output.append(replacement)
                found.add(old_key)
                file_changed |= replacement != old
            else:
                output.append(old)
        if file_changed:
            atomic_write(path, output)
            changed.append(name)
    if existing_only and not found:
        raise ValueError("Không tìm thấy key cũ trong vip.txt/legacy_vip.txt")
    if not existing_only and requested_key not in found:
        vip = folder / "vip.txt"
        lines = read_lines(vip)
        lines.append(line)
        atomic_write(vip, lines)
        changed.append("vip.txt")
    return {"targets": sorted(targets), "found": sorted(found), "changed": sorted(set(changed))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Cấp/cập nhật gói TAXSOFT an toàn")
    parser.add_argument("mode", choices=("issue", "update"))
    parser.add_argument("--folder", type=Path, default=Path("/opt/keys_app/GSOFT"))
    parser.add_argument("--key", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--expires", required=True, help="dd/mm/YYYY")
    parser.add_argument("--contact", default="")
    parser.add_argument("--mst", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key = str(args.key).strip()
    if args.mode == "issue" and not KEYV2_RE.fullmatch(key):
        raise SystemExit("Cấp mới yêu cầu KEYV2 do TAXSOFT hiển thị")
    line = build_line(key, args.plan, args.expires, args.contact, args.mst)
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "line": line}, ensure_ascii=False))
        return 0
    with process_lock(args.folder / ".license_v2.lock"):
        result = update_registry(args.folder, line, existing_only=args.mode == "update")
    print(json.dumps({"ok": True, "mode": args.mode, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
