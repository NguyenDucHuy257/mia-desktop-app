from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .mia_license_v2 import MiaLicenseService


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MIA legacy licenses without modifying vip.txt")
    parser.add_argument("vip_file", type=Path)
    parser.add_argument("--database", type=Path, default=Path("MIA/license.db"))
    args = parser.parse_args()
    secret = os.environ.get("MIA_LICENSE_TOKEN_SECRET", "").encode("utf-8")
    if len(secret) < 32:
        raise SystemExit("MIA_LICENSE_TOKEN_SECRET must contain at least 32 bytes")
    lines = args.vip_file.read_text(encoding="utf-8").splitlines()
    service = MiaLicenseService(args.database, token_secret=secret)
    counts = service.import_legacy_records(lines)
    print(json.dumps({"imported_input_rows": sum(counts.values()), "schema_counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
