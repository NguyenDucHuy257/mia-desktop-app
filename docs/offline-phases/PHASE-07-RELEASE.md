# Phase 7 — Installer production offline

Branch: `feat/offline-phase-07-release`

## Phạm vi

- NSIS x64 chứa Electron, Python runtime, crawler, browser binaries, model, migrations và assets.
- Người dùng không phải cài Python/PostgreSQL riêng.
- Runtime health check trước khi mở workflow.
- Update channel, update atomic và rollback an toàn.
- Code signing khi certificate được cung cấp qua secret manager.
- Quét secret trong `app.asar`, Python package, model và installer.

## Test bắt buộc trên Windows sạch

- Cài mới, mở, đóng/mở lại và gỡ cài đặt.
- Thêm tài khoản, login, đồng bộ nhỏ, resume và xem kết quả.
- Tải riêng, tải toàn bộ Excel, XML, HTML và PDF.
- Python crash, mất mạng và disk gần đầy.
- Update từ phiên bản trước, mất mạng khi update và rollback.
- Kiểm tra process/file còn sót và chữ ký số.

## Gate

- Full workflow chạy trên Windows sạch.
- Không có secret trong artifact.
- Nếu thiếu certificate: `BLOCKED: code-signing certificate`; installer chỉ là bản test.
