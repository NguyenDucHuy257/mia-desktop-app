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
