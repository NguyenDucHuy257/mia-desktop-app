# Phase 0 — Kiến trúc và kiểm kê offline

Branch: `feat/offline-phase-00-architecture`

## Phạm vi

- Kiểm kê desktop và backend tại commit được chốt trước khi triển khai.
- Phân loại crawler, parser, CAPTCHA/model và artifact module có thể tái sử dụng.
- Loại FastAPI route, API authentication, PostgreSQL adapter, cloud worker và deployment config khỏi runtime desktop.
- Thiết kế JSON-RPC Electron main ↔ Python qua `stdin/stdout`.
- Thiết kế SQLite schema cho account, job, progress, result, artifact và setting.
- Kiểm kê license, native dependency, browser binary, model và dung lượng installer.

## Test bắt buộc

- Dependency và license audit.
- Secret scan toàn bộ mã được chọn.
- Prototype start/stop Python child process.
- JSON-RPC request, response, error, timeout, malformed và oversized message.
- Child process crash và shutdown khi đóng Electron.

## Gate

- Kiến trúc, [protocol](OFFLINE-RUNTIME-PROTOCOL.md), [SQLite schema](OFFLINE-SQLITE-SCHEMA.md) và [source audit](OFFLINE-SOURCE-AUDIT.md) được tài liệu hóa.
- Không có secret hoặc deployment credential trong mã được chọn.
- Prototype chạy trên Windows và không để process mồ côi.
- CAPTCHA/model/template vẫn bị chặn cho tới khi xác minh license/provenance.
