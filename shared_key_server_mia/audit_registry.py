"""Read-only registry audit. Prints aggregate counts only, never keys/phones/MSTs."""
from collections import Counter
import json
from pathlib import Path
import sys

from shared_key_server_mia.mia_v2 import V1_RE, OBSERVED_V2_RE, _entitlements, _expiry


def audit(path: Path) -> dict:
    counts = Counter()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key = line.split("|", 1)[0].strip()
        if not key.lower().startswith("key"):
            continue
        counts["key_records"] += 1
        counts["new_key" if key.startswith("KEYV2-") else "supported_legacy" if V1_RE.fullmatch(key) or OBSERVED_V2_RE.fullmatch(key) else "unsupported_legacy_shape"] += 1
        try:
            policy = _entitlements(line)
            counts["trial" if policy["trial"] else "paid"] += 1
            counts["restricted_mst" if policy["allowed_tax_codes"] else "unrestricted_mst"] += 1
        except ValueError:
            counts["invalid_policy"] += 1
        parts = line.split("|")
        if len(parts) > 1 and _expiry(line) == parts[1].strip():
            counts["legacy_expiry_second_column"] += 1
    return dict(counts)


if __name__ == "__main__":
    print(json.dumps(audit(Path(sys.argv[1])), indent=2))
