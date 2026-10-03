"""Snapshot active pre-KEYV2 GSOFT rows before rolling out a new TAXSOFT client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from license_admin import atomic_write, process_lock, read_lines


def is_legacy_gsoft_row(line: str) -> bool:
    key = str(line or "").split("|", 1)[0].strip()
    return key.startswith("KEY") and not key.startswith("KEYV2-")


def seed(folder: Path, *, dry_run: bool = False) -> dict:
    vip_path = folder / "vip.txt"
    legacy_path = folder / "legacy_vip.txt"
    vip_lines = read_lines(vip_path)
    legacy_lines = read_lines(legacy_path)
    existing = {line.split("|", 1)[0].strip() for line in legacy_lines}
    additions = []
    for line in vip_lines:
        if not is_legacy_gsoft_row(line):
            continue
        key = line.split("|", 1)[0].strip()
        if key in existing:
            continue
        additions.append(line)
        existing.add(key)
    if additions and not dry_run:
        atomic_write(legacy_path, [*legacy_lines, *additions])
    return {
        "ok": True,
        "source_rows": len(vip_lines),
        "existing_legacy_rows": len(legacy_lines),
        "added": len(additions),
        "final_legacy_rows": len(legacy_lines) + len(additions),
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed GSOFT legacy key snapshot")
    parser.add_argument("--folder", type=Path, default=Path("/opt/keys_app/GSOFT"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with process_lock(args.folder / ".license_v2.lock"):
        result = seed(args.folder, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
