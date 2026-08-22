import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "jsonrpc": "2.0",
        "method": "export.progress",
        "params": {
            "status": "running",
            "scope": "details",
            "phase": "write_rows",
            "processed": 2,
            "total": 4,
            "percent": 50.0,
        },
    }), flush=True)
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"ok": True},
    }), flush=True)
