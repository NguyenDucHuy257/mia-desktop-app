# Kế hoạch tách MIA Desktop

## Nguồn đã kiểm kê

Backend production: `hvsoftware26/mia-crawl-service`, commit `63acf111c64b47ac964608141b2c83bbb6e2f688`. Không copy crawler, worker, PostgreSQL adapter, CAPTCHA model, deployment scripts hay credential sang desktop.

Figma: file `AvrM4nHgn649ZZeoLei2vN`, page `0:1`. Frame chính `1:2`, kích thước 1500×1024, màu chủ đạo `#166534`, sidebar 210 px, font Inter.

## Phase và cổng kiểm thử

| Phase | Phạm vi | Gate bắt buộc |
|---|---|---|
| 0 — Foundation | Repo, Electron sandbox, React/Vite, contract types, Figma baseline, CI | typecheck + unit + web build |
| 1 — Shell Figma | Sidebar/topbar/Quản lý HĐĐT tại 1500×1024 | visual diff ≤3% ở môi trường chuẩn |
| 2 — Tài khoản | create/get/reconnect/revoke connection, form và trạng thái | mock contract + API staging smoke |
| 3 — Job lifecycle | create/idempotency/poll/cancel/progress hai cấp | state-machine unit + restart/poll E2E |
| 4 — Kết quả | overview/detail cursor pagination, tìm kiếm/lọc | pagination contract + 421-item regression |
| 5 — XML/HTML/PDF/MVT | Các frame Figma còn lại và output local | artifact integrity + path traversal tests + visual tests từng tab |
| 6 — Xác minh máy | activation, Ed25519 challenge, DPAPI, token rotation/revoke | replay/clone/revoke/offline-grace security tests |
| 7 — EXE | NSIS x64, code signing, updater, rollback | clean Windows VM install/update/uninstall smoke |

Không sang phase tiếp theo khi gate hiện tại đỏ. Test portal thật chỉ dùng khoảng nhỏ và tài khoản test được phép; không ghi API key, password, token, MST hay payload hóa đơn vào log CI.

## Ánh xạ frame Figma

| Node | Màn hình/trạng thái | Phase |
|---|---|---|
| `1:2` | Giao diện chính / Quản lý HĐĐT | 1 |
| `4:628`, `4:654` | Option và Declaration type | 1–3 |
| `60:1287` | Thêm tài khoản | 2 |
| `1:466` | Tổng quan | 4 |
| `85:16452` | Chi tiết | 4 |
| `1:654` | XML Downloader | 5 |
| `104:22` | HTML Downloader | 5 |
| `106:18856` | PDF Downloader | 5 |
| `106:19444` | PDF Converting | 5 |

Mỗi phase đi theo nhánh `feat/phase-XX-*`, có PR riêng và không merge khi một trong các gate type, unit, contract, visual hoặc packaging liên quan bị đỏ.
