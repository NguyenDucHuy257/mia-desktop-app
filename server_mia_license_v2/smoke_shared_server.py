from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def post_json(url: str, payload: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2 * 1024 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only shared key-server namespace smoke")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("Shared-server smoke requires HTTPS")
    results = {}
    for tool in ("MIA", "MIA2", "MIA3", "GBOT", "IDQUICK", "GSOFT"):
        status, body = post_json(base + "/verify-key", {"tool": tool})
        results[tool] = {"status": status, "nonempty": bool(body.strip())}
    print(json.dumps(results, sort_keys=True))
    if any(item["status"] != 200 for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
