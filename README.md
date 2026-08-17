# MIA WT Desktop

Desktop client tách riêng cho backend `mia-crawl-service`. Ứng dụng dùng Electron + React + TypeScript, không chứa crawler và không nhúng credential production.

## Chạy local

```bash
npm ci
npm run verify
npm run dev:electron
```

## Kiểm thử

```bash
npm run typecheck
npm test
npm run build:web
npm run test:visual
```

Visual baseline lấy trực tiếp từ frame Figma `1:2` ở 1500×1024. Gate hiện tại là tối đa 3% pixel khác biệt; kết quả phải được xác nhận lại trên Windows runner cố định trước khi phát hành.

Xem [kiểm kê backend](docs/REPO-AUDIT.md), [kiến trúc desktop](docs/ARCHITECTURE.md), [kế hoạch migration](docs/MIGRATION-PLAN.md), [API contract](docs/API-CONTRACT.md), [quy trình Figma](docs/FIGMA-WORKFLOW.md) và [báo cáo kiểm thử](docs/TEST-REPORT.md).
