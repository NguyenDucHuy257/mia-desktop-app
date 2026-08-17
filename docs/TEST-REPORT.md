# Báo cáo kiểm thử Phase 0–1

Ngày chạy: 2026-08-17.

| Gate | Kết quả |
|---|---|
| TypeScript project references | PASS |
| Electron CJS syntax (main/preload/device/security) | PASS |
| Vitest | PASS — 4 file, 9 test |
| Vite production build | PASS — 41 module |
| Playwright visual 1500×1024 | PASS — gate 3% |
| npm audit production | PASS — 0 advisory |
| npm audit toàn bộ dependency | PASS — 0 advisory |

Visual test dùng baseline lấy trực tiếp từ Figma frame `1:2`, threshold màu 0.25. Khi cố tình siết gate xuống 2%, Playwright báo 32.550/1.536.000 pixel khác biệt, tương đương khoảng 2,12%; vì vậy gate 3% hiện tại có biên thực tế, không phải baseline tự cập nhật.

Chưa chạy trong môi trường này:

- Windows NSIS packaging/install/uninstall smoke: đã cấu hình job `windows-package` trong CI nhưng cần repository GitHub để chạy runner Windows.
- API staging/portal smoke: thuộc Phase 2+, cần account test được phép và contract activation thiết bị phía backend.
- Code signing/updater: thuộc Phase 7 và cần certificate/release channel.
