# MIA WT Desktop

Desktop client tách riêng cho backend `mia-crawl-service`. Ứng dụng dùng Electron + React + TypeScript, không chứa crawler và không nhúng credential production. Phase 2 đã có màn hình thêm tài khoản đơn lẻ/hàng loạt và Electron main-process broker cho `account-connections`.

## Chạy local

```bash
npm ci
npm run verify
npm run dev:electron
```

API staging chỉ được cấu hình bằng biến môi trường của Electron main process:

```bash
MIA_API_BASE_URL=https://crawl-staging.example.com \
MIA_API_ACCESS_TOKEN=temporary-staging-token \
npm run dev:electron
```

Không dùng biến `VITE_*` cho token. Browser preview không gọi API thật; thêm `?demo=1` vào URL local chỉ để test tương tác form bằng adapter in-memory.

## Kiểm thử

```bash
npm run typecheck
npm test
npm run build:web
npm run test:visual
```

Visual baseline lấy trực tiếp từ Figma ở 1500×1024. Frame chính `1:2` có gate tối đa 3%; form đơn lẻ `1:368` và hàng loạt `60:1182` có gate chặt hơn là 1%. Build còn quét renderer bundle và thất bại nếu phát hiện marker token/header API.

Smoke test `create/get/reconnect/revoke` chỉ chạy với tài khoản staging chuyên dụng. Lệnh này revoke connection sau khi kiểm tra và yêu cầu xác nhận rõ:

```bash
MIA_SMOKE_ALLOW_REVOKE=dedicated-test-account npm run smoke:accounts
```

Xem [kiểm kê backend](docs/REPO-AUDIT.md), [kiến trúc desktop](docs/ARCHITECTURE.md), [kế hoạch migration](docs/MIGRATION-PLAN.md), [API contract](docs/API-CONTRACT.md), [quy trình Figma](docs/FIGMA-WORKFLOW.md) và [báo cáo kiểm thử](docs/TEST-REPORT.md).
