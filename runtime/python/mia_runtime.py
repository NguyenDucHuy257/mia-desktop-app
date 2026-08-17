"""Minimal offline runtime used to validate the Electron/Python boundary."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = "1.0"
RUNTIME_VERSION = "0.1.0"


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        encoded = json.dumps({
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32603, "message": "response_too_large"},
        }, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def validate_request(value: Any) -> tuple[str | int, str, Any]:
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise RpcError(-32600, "invalid_request")
    request_id = value.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise RpcError(-32600, "invalid_request_id")
    method = value.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(-32600, "invalid_method")
    params = value.get("params", {})
    if not isinstance(params, (dict, list)):
        raise RpcError(-32602, "invalid_params")
    return request_id, method, params


def dispatch(method: str, params: Any) -> tuple[Any, bool]:
    if method == "system.health":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "pid": os.getpid(),
        }, False
    if method == "system.echo":
        return params, False
    if method == "system.sleep":
        if not isinstance(params, dict) or isinstance(params.get("milliseconds"), bool):
            raise RpcError(-32602, "invalid_params")
        milliseconds = params.get("milliseconds")
        if not isinstance(milliseconds, int) or not 0 <= milliseconds <= 5000:
            raise RpcError(-32602, "invalid_duration")
        time.sleep(milliseconds / 1000)
        return {"slept_ms": milliseconds}, False
    if method == "system.shutdown":
        return {"accepted": True}, True
    raise RpcError(-32601, "method_not_found")


def serve() -> int:
    should_stop = False
    while not should_stop:
        raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 2)
        if not raw:
            break
        if len(raw) > MAX_MESSAGE_BYTES + 1 or not raw.endswith(b"\n"):
            write_message({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "request_too_large"},
            })
            return 64

        request_id: str | int | None = None
        try:
            decoded = json.loads(raw.decode("utf-8"))
            request_id, method, params = validate_request(decoded)
            result, should_stop = dispatch(method, params)
            write_message({"jsonrpc": "2.0", "id": request_id, "result": result})
        except UnicodeDecodeError:
            write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}})
        except json.JSONDecodeError:
            write_message({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}})
        except RpcError as error:
            write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": error.message}})
        except Exception:
            write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "internal_error"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
