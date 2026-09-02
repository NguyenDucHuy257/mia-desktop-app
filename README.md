# MIA TOOL 2026 Desktop

Ứng dụng Electron + React + TypeScript đang được chuyển sang kiến trúc chạy crawler cục bộ. Kiến trúc mục tiêu không gọi HTTP API nghiệp vụ:

```text
React renderer -> IPC allowlist -> Electron main -> JSON-RPC -> Python runtime -> SQLite
```

Node.js bắt buộc từ phiên bản 24. Python system chỉ cần trên máy phát triển; Phase 1 đóng gói `mia-runtime.exe` vào installer để máy người dùng không phải cài Python.

## Chạy local

```powershell
npm ci
npm run verify
npm run dev:electron
```

## Kiểm thử

```powershell
npm run typecheck
npm run check:electron
npm run check:runtime
npm run check:offline-boundary
npm test
npm run build:web
npm run check:renderer-bundle
npm run test:visual
npm audit --omit=dev
npm audit
npm run package:win
```

`check:offline-boundary` chặn endpoint, credential assignment và các artifact/model chưa được duyệt lọt vào Python runtime. Renderer vẫn được quét riêng để ngăn secret/header API lọt vào bundle.

## Phase 0

Phase 0 cung cấp JSON-RPC prototype bằng Python standard library với health, echo, timeout và graceful shutdown. Electron client không dùng shell, giới hạn message 1 MiB, chỉ truyền environment allowlist và dừng runtime khi gặp protocol violation.

Crawler, CAPTCHA/model, template và dependency backend chưa được copy trong Phase 0. Chúng chỉ được nhập sau khi dependency, license, provenance và secret audit đạt gate.

Xem:

- [Roadmap](docs/MIGRATION-PLAN.md)
- [Kiến trúc](docs/ARCHITECTURE.md)
- [Phase 0](docs/offline-phases/PHASE-00-ARCHITECTURE.md)
- [Runtime protocol](docs/offline-phases/OFFLINE-RUNTIME-PROTOCOL.md)
- [SQLite schema](docs/offline-phases/OFFLINE-SQLITE-SCHEMA.md)
- [Source audit](docs/offline-phases/OFFLINE-SOURCE-AUDIT.md)
- [Báo cáo test](docs/TEST-REPORT.md)
- [Hướng dẫn kiểm chứng tay Phase 1](docs/offline-phases/PHASE-01-MANUAL-TEST.md)
