from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

PHONE_RE = re.compile(r"^0[0-9]{9}$")
V1_RE = re.compile(r"^key([0-9a-f]{29})$")
V2_RE = re.compile(r"^KEY([0-9a-f]{29})(0[0-9]{9})$")
FULL_HASH_RE = re.compile(r"^key[0-9a-f]{64}$")
MAK_RE = re.compile(r"^MAK-(?:[A-Za-z0-9]{4}-){4}[A-Za-z0-9]{4}$")


@dataclass(frozen=True)
class LegacyRecord:
    raw_line: str
    key_hash: str
    schema_name: str
    machine_hash29: str | None
    phone_guess: str | None
    expires_at: str | None
    parse_status: str


def _parse_expiry(value: str, today: date) -> tuple[str | None, str]:
    if not value:
        return None, "manual"
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None, "manual"
    iso = parsed.isoformat()
    return iso, "expired" if parsed < today else "ready"


def parse_legacy_line(raw_line: str, *, today: date | None = None) -> LegacyRecord:
    today = today or date.today()
    raw_line = str(raw_line).rstrip("\r\n")
    parts = raw_line.split("|")
    key = parts[0].strip() if parts else ""
    expiry, parse_status = _parse_expiry(parts[2].strip() if len(parts) > 2 else "", today)
    phone_candidates = [part.strip() for part in parts[1:] if PHONE_RE.fullmatch(part.strip())]
    phone_guess = phone_candidates[0] if len(set(phone_candidates)) == 1 else None
    machine_hash29 = None

    match = V1_RE.fullmatch(key)
    if match:
        schema = "mia_v1_disk_hash29"
        machine_hash29 = match.group(1)
    else:
        match = V2_RE.fullmatch(key)
        if match:
            schema = "mia_v2_hash29_phone_observed"
            machine_hash29 = match.group(1)
            phone_guess = match.group(2)
        elif FULL_HASH_RE.fullmatch(key):
            schema = "mia_full_hash_pending"
            parse_status = "manual"
        elif MAK_RE.fullmatch(key):
            schema = "mia_mak_pending"
            parse_status = "manual"
        else:
            schema = "mia_custom_manual"
            parse_status = "manual"

    return LegacyRecord(
        raw_line=raw_line,
        key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        schema_name=schema,
        machine_hash29=machine_hash29,
        phone_guess=phone_guess,
        expires_at=expiry,
        parse_status=parse_status,
    )


def parse_legacy_lines(lines: Iterable[str], *, today: date | None = None) -> list[LegacyRecord]:
    return [parse_legacy_line(line, today=today) for line in lines if str(line).strip()]
