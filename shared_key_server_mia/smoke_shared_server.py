from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib import request


def load(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("smoke fixture must be a JSON object")
    return value


def post(base_url: str, endpoint: str, payload: dict) -> tuple[int, int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(base_url.rstrip("/") + endpoint, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            content = response.read(1024 * 1024)
            status = response.status
    except Exception as exc:
        status = int(getattr(exc, "code", 0) or 0)
        content = getattr(exc, "read", lambda: b"")()
    reason = ""
    try:
        parsed = json.loads(content)
        reason = str(parsed.get("reason") or parsed.get("detail") or "")[:80] if isinstance(parsed, dict) else ""
    except Exception:
        pass
    return status, len(content), reason


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the existing shared key server without printing secrets")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--legacy-gsoft", required=True)
    parser.add_argument("--legacy-mia", required=True)
    parser.add_argument("--v2-gsoft", required=True)
    parser.add_argument("--v2-mia", required=True)
    args = parser.parse_args()
    checks = (
        ("legacy-GSOFT", "/verify-key", load(args.legacy_gsoft)),
        ("legacy-MIA", "/verify-key", load(args.legacy_mia)),
        ("v2-GSOFT", "/verify-key-v2", load(args.v2_gsoft)),
        ("v2-MIA", "/verify-key-v2", load(args.v2_mia)),
    )
    failed = False
    for name, endpoint, payload in checks:
        status, size, reason = post(args.base_url, endpoint, payload)
        ok = status == 200
        failed |= not ok
        print(f"{name}: {'PASS' if ok else 'FAIL'} status={status} bytes={size} reason={reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
