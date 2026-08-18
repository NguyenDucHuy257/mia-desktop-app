# Phase 4A — Báo cáo crawler offline

Ngày kiểm tra: 2026-08-18  
Branch: `feat/offline-phase-04a-crawler`

## Nguồn và boundary

- Nguồn được pin tại commit `64ebb6ec0a35c784e194e8ce116c4bb4cf1b19d3` của `hvsoftware26/mia-crawl-service`.
- Nhập byte-for-byte 45 file thuộc `app/**` và `resources/**`; manifest SHA-256 được kiểm tra trong CI.
- Không nhập FastAPI/server, HTTP authentication của service, deployment, `.env`, credential hay runtime data.
- Adapter desktop nằm ngoài vendor, nên source nghiệp vụ không bị sửa trực tiếp.

## Luồng đã nối

- Electron main lấy ciphertext từ SQLite, giải mã bằng `safeStorage` trong main và truyền password qua stdio local; renderer không nhận secret.
- Worker nền đăng nhập portal, giải CAPTCHA bằng model thật, lấy tên công ty và cập nhật account.
- Overview chạy theo date range, purchase/sold và query/sco-query đã chọn; raw data được upsert vào SQLite của desktop để Results UI đọc.
- Job có idempotency, resume, progress đơn điệu, trạng thái terminal, cancel flag và lỗi sanitize.
- Packaged runtime chứa Torch, torchvision, PyQt5, Playwright, model và template; runtime hook chuẩn bị DLL Torch trước khi load.

## Test

- Backend reference: `PASS` — 153 test, 1 skipped.
- Desktop build: `PASS` — Vitest 61/61, Python storage 10/10, typecheck, boundary/hash, web build và secret scan.
- Vendored source integrity: `PASS` — 45/45 file.
- Packaged runtime health/storage/CAPTCHA model load: `PASS`.
- Portal smoke bằng tài khoản thật: `NOT RUN` — credential không được đặt trong source, log hoặc command; cần nhập qua UI/safeStorage khi kiểm chứng installer.

## Còn lại trước khi merge

- Bổ sung detail pipeline vào cùng coordinator và test cancel ở giữa request dài.
- Chạy installer smoke với tài khoản được phép qua UI, khoảng ngày nhỏ, rồi đối soát portal → SQLite → Results.
