# Phase 4-UI — Báo cáo triển khai

Ngày kiểm tra: 2026-08-18  
Branch: `feat/offline-phase-04-ui-figma`

## Phạm vi

- Hoàn thiện các màn hình XML `1:654`, HTML `104:22`, PDF `106:18856` và PDF progress `106:19444`.
- Dùng asset và baseline xuất trực tiếp từ Figma.
- Hoàn thiện date range, chọn công ty, lọc mua vào/bán ra, phân trang, tải hàng loạt, thao tác từng dòng và hủy PDF.
- Hoàn thiện search, status filter và pagination của màn hình hóa đơn bằng local result adapter.

## Kết quả test

- Typecheck: `PASS`.
- Electron syntax/security checks: `PASS`.
- Vitest: `PASS` — 60/60.
- Build web và renderer secret scan: `PASS`.
- Playwright interaction/visual/responsive: `PASS` — 21/21.
- Visual 1500×1024: `PASS` — cả bốn frame mới không vượt quá 1%.
- Linux/Chromium ghi nhận HTML 15.517 pixel khác và PDF progress 20.485 pixel khác tại color threshold 0,25 do anti-aliasing font/SVG; PDF progress còn 19.380 pixel ở threshold 0,30. Baseline và gate không được nới. Vì sản phẩm đích là Windows và cả hai frame PASS trên Chromium/Windows ở threshold 0,25, CI visual chạy trên `windows-latest` với `maxDiffPixelRatio: 0.01`.
- Responsive 1024/1280/1366/1440/1500/1600: `PASS`.
- `npm audit --omit=dev` và `npm audit`: `PASS` — 0 vulnerability.
- Packaged Python runtime smoke: `PASS`.
- NSIS local: `FAIL` — Windows từ chối rename `release/win-unpacked.tmp`; xác minh lại bằng job `windows-package` sạch trên CI.
- Portal/crawler thật: `NOT RUN` theo boundary Phase 4-UI; chuyển sang Phase 4A.

Không có credential, MST thật, payload hóa đơn thật hoặc secret được đưa vào source, test, screenshot hay log.
