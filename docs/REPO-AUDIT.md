# Kiểm kê `mia-crawl-service`

## Nguồn production được chọn

- Repository: `hvsoftware26/mia-crawl-service` (private).
- Default branch `main` không phải mốc mới nhất đã được deploy.
- Mốc desktop bám theo: branch `fix/direct-final-route-fallback-20260812`, commit `63acf111c64b47ac964608141b2c83bbb6e2f688` ngày 2026-08-12.
- PR #182 chứa build/test của thay đổi; PR #183 chứa bước deploy và smoke test.
- Tree tại mốc này có 282 entry và 46 file test.

## Biên API đã xác nhận từ source

| Nhóm | Module backend | Desktop dùng |
|---|---|---|
| Health | `app/external_api` | live/ready probe |
| Tài khoản | `app/account_connections` | create/get/reconnect/revoke |
| Job | `app/job_engine` | create/status/cancel |
| Kết quả | `app/external_api` | overview/details cursor pages |
| Auth nội bộ | `app/auth_service` | Không copy; cần contract license mới |
| Worker/crawler | `worker_runtime` và adapter portal | Không copy sang EXE |
| Deploy/data | scripts, PostgreSQL, model, secrets | Không copy sang EXE |

Contract hiện tại yêu cầu `Idempotency-Key` khi tạo job và dùng `connection_id`; các field cũ như session ID, username/password trong job, proxy và `detail_limit` bị từ chối. `/v1/sessions` đã deprecated và sunset 2026-12-01.

## Quyết định tách repo

Desktop là một release train, trust boundary và công nghệ đóng gói khác backend, vì vậy tạo repo riêng thay vì copy toàn bộ backend. Repo desktop chỉ giữ UI, Electron main/preload, API contract, lưu file local, bộ xác minh thiết bị và test. Backend tiếp tục chịu trách nhiệm crawler, credential portal, queue, database và vận hành production.

## Rủi ro phải chặn trước EXE

1. Không nhúng `X-MIA-API-Key` dài hạn vào renderer, preload, source map, `app.asar` hay biến `VITE_*`.
2. Cần API activation/challenge/token trước khi desktop có thể gọi production an toàn.
3. Job phải khôi phục được sau khi app restart và mọi request create phải idempotent.
4. Output local phải chống path traversal, filename collision và file ghi dở.
5. Installer cần code signing và smoke test trên Windows VM sạch.
