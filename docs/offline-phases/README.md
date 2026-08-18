# Roadmap offline — quy tắc chung

## Nguyên tắc

- Không gọi HTTP API nghiệp vụ để crawl, đọc kết quả hoặc tạo artifact.
- React chỉ gọi IPC allowlist; Electron main xác minh sender và validate lại DTO.
- Python chạy dưới dạng child process riêng và giao tiếp JSON-RPC qua `stdin/stdout`.
- Dùng SQLite thay PostgreSQL cho dữ liệu local.
- Không đưa credential, deployment config hoặc secret vào source, log, fixture hay installer.
- Chỉ tái sử dụng mã backend sau dependency, license và secret audit.
- Mỗi phase có branch `feat/offline-phase-XX-*`, commit và PR riêng.
- Chỉ tạo phase tiếp theo từ `origin/main` sau khi phase hiện tại merge và CI xanh.

## Checklist chung trước PR

```powershell
npm run typecheck
npm run check:electron
npm run test
npm run build:web
npm run check:renderer-bundle
npm run test:visual
npm audit --omit=dev
npm audit
npm run package:win
```

Ngoài ra phải kiểm tra child-process cleanup, secret scan cho mã Python, SQLite migration, artifact path safety và diff chỉ thuộc phase hiện tại.

## Trạng thái test

- `PASS`: đã chạy và có bằng chứng.
- `FAIL`: đã chạy nhưng lỗi.
- `NOT RUN`: chưa chạy.
- `BLOCKED`: thiếu điều kiện bên ngoài không thể thay thế hợp lệ.

Không cập nhật baseline visual bằng ảnh của app để che regression. Baseline phải xuất trực tiếp từ Figma.
