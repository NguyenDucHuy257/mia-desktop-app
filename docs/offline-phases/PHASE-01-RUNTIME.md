# Phase 1 — Python runtime và SQLite

Branch: `feat/offline-phase-01-runtime`

## Phạm vi

- Tạo Python package riêng, không đưa Python hoặc crawler vào renderer.
- Đóng gói runtime Python cho Windows.
- Xây SQLite repositories và versioned migrations.
- Electron main quản lý start, health, restart có giới hạn và shutdown.
- Log có redaction, rotation và request correlation không chứa dữ liệu nhạy cảm.

## Test bắt buộc

- Start/stop và chống chạy trùng process.
- Crash, timeout, bounded restart và cancel request.
- Malformed/oversized JSON-RPC.
- Migration database mới, cũ, bị khóa và bị hỏng.
- Transaction bị gián đoạn và phục hồi sau restart.
- Renderer bundle không chứa credential, DB data hoặc Python runtime path nhạy cảm.

## Gate

- Runtime hoạt động trên Windows sạch.
- Đóng app dừng toàn bộ child process/Chromium.
- SQLite phục hồi nhất quán sau restart.

## Kiểm chứng tay

Thực hiện checklist tại [PHASE-01-MANUAL-TEST.md](PHASE-01-MANUAL-TEST.md) trên một Windows VM không cài Python. Automated CI packaging không thay thế gate clean-VM này.
