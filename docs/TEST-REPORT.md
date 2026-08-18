# Báo cáo kiểm thử Phase 0–2

Ngày chạy: 2026-08-17.

## Baseline đã xác nhận trên `main` (Phase 0–1)

| Gate | Kết quả |
|---|---|
| TypeScript project references | PASS |
| Electron CJS syntax (main/preload/device/security) | PASS |
| Vitest | PASS — 4 file, 9 test |
| Vite production build | PASS — 41 module |
| Playwright visual 1500×1024 | PASS — gate 3% |
| Windows NSIS x64 packaging | PASS — GitHub Actions |
| npm audit production | PASS — 0 advisory |
| npm audit toàn bộ dependency | PASS — 0 advisory |

## Nhánh Phase 2

| Gate | Kết quả local |
|---|---|
| TypeScript + Electron syntax | PASS |
| Vitest account form/gateway/broker/main API | PASS — tổng 8 file, 21 test |
| Vite production build | PASS — 45 module |
| Renderer secret scan | PASS |
| Playwright interaction đơn lẻ/hàng loạt | PASS |
| Visual `1:368` | PASS — 9.186/1.536.000 pixel, khoảng 0,60%; gate 1% |
| Visual `60:1182` | PASS — 9.844/1.536.000 pixel, khoảng 0,64%; gate 1% |

Hai baseline tài khoản được lấy trực tiếp từ Figma ở đúng 1500×1024; không lấy ảnh app làm baseline. Sai lệch còn lại tập trung ở anti-alias font/icon giữa Figma renderer và Chromium.

Visual test dùng baseline lấy trực tiếp từ Figma frame `1:2`, threshold màu 0.25. Khi cố tình siết gate xuống 2%, Playwright báo 32.550/1.536.000 pixel khác biệt, tương đương khoảng 2,12%; vì vậy gate 3% hiện tại có biên thực tế, không phải baseline tự cập nhật.

GitHub Actions run `32031173453` trên `main` đã hoàn tất thành công cả ba job `verify`, `visual` và `windows-package`. Artifact unsigned `mia-wt-windows-unsigned` có kích thước khoảng 103,6 MB và được giữ đến 2026-08-31.

Chưa chạy:

- Cài đặt/gỡ cài đặt trên Windows VM sạch và kiểm tra app sau khi mở.
- API staging/portal smoke: script đã có, chưa chạy vì cần account test chuyên dụng và token staging được phép.
- Code signing/updater: thuộc Phase 7 và cần certificate/release channel.

## Phase 3 — kết quả local

- Baseline trước phát triển: PASS — 21 unit, 4 Playwright; sửa command webServer Playwright sang npm script để chạy đa nền tảng Windows/Linux, không đổi ảnh baseline.
- Job state machine/broker/API: PASS — bao phủ 9 backend status, validation, sanitize, idempotency, crash/restart resume, cancel và progress clamp.
- Interaction: PASS — tạo, poll hai cấp, cancel và lỗi mạng tạm thời/retry.
- Visual node Phase 3: ACCEPTED EXCEPTION — người dùng chấp nhận ngày 2026-08-18. `4:628` lệch khoảng 1.755/25.704 pixel (6,83%), gate 8%; `4:654` lệch 1.942/17.952 pixel (10,82%), gate 12%, threshold màu 0.25. Baseline vẫn là PNG export trực tiếp từ Figma, không cập nhật bằng ảnh app. Gate màn hình chính và hai màn hình tài khoản giữ nguyên 3%/1%.
- Staging small-job smoke: NOT RUN — thiếu tài khoản staging chuyên dụng được phép.

## Offline Phase 0 — kiến trúc và runtime prototype

- Node.js: PASS — `v24.19.0`.
- Python prototype: PASS — Python `3.11.9`, syntax compile không tạo bytecode trong repo.
- Source/dependency/license audit: PASS WITH BLOCKERS — đã đối chiếu backend production `63acf111…`; không copy backend source. PyQt5 GPL, CAPTCHA model và Excel template chưa rõ provenance/license nên bị chặn khỏi runtime.
- JSON-RPC protocol + SQLite schema: PASS — đã tài liệu hóa protocol v1, giới hạn 1 MiB, lifecycle/error và schema mục tiêu.
- Runtime boundary unit: PASS — health/echo/error, timeout/recovery, crash, malformed/oversized response, oversized request, shutdown và environment allowlist.
- Unit regression: PASS — 11 file, 48 test.
- Electron/Python syntax: PASS.
- Offline boundary scan: PASS.
- Renderer secret scan: PASS.
- Vite build: PASS — 48 module.
- Playwright: PASS — 8/8; không thay đổi baseline Figma.
- `npm audit --omit=dev`: PASS — 0 vulnerability.
- `npm audit`: PASS — 0 vulnerability.
- Windows NSIS x64 local: PASS — installer test tạo thành công; Phase 0 chưa đóng gói Python runtime/model/browser.
- Windows clean-VM runtime test: NOT RUN — thuộc Phase 1 sau khi có Python runtime đóng gói.
- Code signing: NOT RUN — thuộc Phase 7.

## Offline Phase 1 — Python runtime và SQLite

- Python storage unit: PASS — 5/5; database mới/cũ, locked/corrupt, rollback transaction và log redaction.
- Electron runtime lifecycle: PASS — chống start trùng, health/init, crash recovery giữ database và bounded restart.
- JSON-RPC regression: PASS — protocol/error/timeout/malformed/oversized/shutdown.
- Unit tổng: PASS — 12 file, 51 test.
- PyInstaller one-folder runtime: PASS — tránh extraction process của one-file; packaged smoke health + migration đạt schema v1/integrity `ok`.
- Runtime process cleanup: PASS — packaged smoke kết thúc không còn process `mia-runtime`.
- Win-unpacked app smoke: PASS — app tạo đúng một runtime process, tạo `offline-runtime/mia.sqlite3`, và runtime về 0 khi Electron process kết thúc.
- NSIS x64 local: PASS — runtime được nhúng tại `resources/runtime/mia-runtime.exe`.
- Electron/Python syntax, offline boundary và renderer secret scan: PASS.
- Playwright: PASS — 8/8; không thay đổi UI/baseline.
- `npm audit --omit=dev` và `npm audit`: PASS — 0 vulnerability.
- Clean Windows manual verification: PASS — người dùng xác nhận ngày 2026-08-18 sau khi kiểm tra theo `PHASE-01-MANUAL-TEST.md`.
- Crawler/CAPTCHA/model: NOT INCLUDED — đúng phạm vi Phase 1 và blocker license/provenance Phase 0.

## Offline Phase 2 — tài khoản cục bộ

- Local account SQLite CRUD: PASS — create/list/get/update/delete, duplicate reuse và DTO không trả ciphertext.
- Electron broker: PASS — validate tại main, DPAPI encrypt trước JSON-RPC, password không có trong response.
- UI interaction: PASS — danh sách trắng, thêm, persist trong gateway, trạng thái `unchecked` và xóa; Playwright 9/9.
- Kiểm chứng tay local account trên Windows: PASS — người dùng nghiệm thu ngày 2026-08-18.
- Bảng tài khoản: PASS — bỏ cột Kỳ tải, thêm cột Tên công ty ngay sau MST; tạm hiển thị `—` cho đến phase tra cứu tự động.
- Unit tổng: PASS — 13 file, 53 test; Python storage 6/6.
- Renderer/offline boundary scan, build và npm audit: PASS.
- Portal login success/failure/CAPTCHA timeout/locked: BLOCKED — chưa nhập crawler/CAPTCHA vì model/template chưa có provenance/license hợp lệ.
- Cross-machine DPAPI clone test: NOT RUN — cần hai Windows identity/máy theo hướng dẫn test tay.
- Phase 2 tổng thể: BLOCKED — không coi account `unchecked` là portal login thành công.

## Offline Phase 3 — job lifecycle

- SQLite schema v2 + event transaction: PASS — create/idempotency/resume/status/summary/cancel/clear và optimistic transition sequence.
- Idempotency qua runtime restart: PASS — cùng key trả cùng `job_id`, không tạo dòng/event trùng.
- State machine: PASS — đủ 9 trạng thái terminal/non-terminal; progress hai cấp clamp 0–100; response sequence cũ không ghi đè state mới.
- Cancel queued/running/cancelling/terminal: PASS — queued thành cancelled, running thành cancelling, cancelling/terminal idempotent.
- Electron IPC allowlist + sanitization: PASS — renderer không truy cập `jobs.transition`, intent lạ/rỗng bị chặn tại main.
- Retry/recovery interaction: PASS — bounded backoff, runtime crash restart/resume và Playwright create/poll/cancel/error/retry.
- Lựa chọn tự do: PASS — mua vào, bán ra, tổng quan và chi tiết có thể chọn/bỏ; job rỗng bị cảnh báo.
- Notification/menu interaction: PASS — modal notice/error, biểu tượng theo loại, nút Đóng, click-outside và `Esc` đóng menu.
- Invoice tab scope: PASS — bỏ menu Hóa đơn/XML/HTML/PDF; job của tab cố định `data_types: ['invoice']`, artifact chuyển sang tab riêng.
- Account table interaction: PASS — checkbox MST tích/bỏ được; nút Thêm tài khoản không xuống dòng.
- Unit hiện tại: PASS — Python storage 9/9; Vitest 57/57; Playwright 10/10.
- Packaged runtime + NSIS x64 local: PASS — schema v2 migration, create/idempotency/resume/cancel smoke và installer `MIA WT Setup 0.1.0.exe`.
- Offline boundary, renderer secret scan và npm audit: PASS — 0 vulnerability.
- Small-job portal thật: BLOCKED — crawler/CAPTCHA artifacts chưa vượt gate provenance/license, không giả lập thành PASS.
- Kiểm chứng tay Windows Phase 3: NOT RUN — theo `PHASE-03-MANUAL-TEST.md`.
