# MIA Desktop API contract

Nguồn chuẩn của contract là `hvsoftware26/mia-crawl-service` tại commit production đã kiểm thử `63acf111c64b47ac964608141b2c83bbb6e2f688` (2026-08-12).

Desktop sử dụng các endpoint sau:

- `GET /health/live`, `GET /health/ready`;
- `POST /v1/account-connections`;
- `GET /v1/account-connections/{connection_id}`;
- `POST /v1/account-connections/{connection_id}/reconnect`;
- `DELETE /v1/account-connections/{connection_id}`;
- `POST /v1/jobs` kèm `Idempotency-Key`;
- `GET /v1/jobs/{job_id}`;
- `GET /v1/jobs/{job_id}/summary`;
- `POST /v1/jobs/{job_id}/cancel`;
- `GET /v1/jobs/{job_id}/results/overview`;
- `GET /v1/jobs/{job_id}/results/details`.

## Phase 3 — job lifecycle

`POST /v1/jobs` chỉ nhận các field của `CreateJobBody` tại commit production: `connection_id`, `date_from`, `date_to`, `directions`, `query_types`, `force_refresh`, `refresh_latest_month`, `result_scope`, `include_xml`, `include_mvt`. `detail_limit` và field lạ bị từ chối tại commit `63acf111…`. `Idempotency-Key` dài 8–256 ký tự; cùng key và cùng normalized request trả cùng `job_id`, khác request trả `409`.

Main process validate lại request và response, persist `job_id`, `connection_id`, intent, idempotency key và timestamp trong `userData/jobs/active-job.json`. Renderer chỉ gọi IPC `jobs.start/resume/status/summary/cancel/clear`; header xác thực và idempotency không đi vào renderer bundle.

Poll status chỉ dùng bảy field công khai: `job_id`, `status`, `stage`, `overall_percent`, `current_month`, `updated_at`, `error`. Terminal gồm `completed`, `completed_with_warning`, `failed`, `cancelled`, `abandoned`.

UI cho phép bật/tắt tự do mọi checkbox, kể cả bỏ hết. Khi submit, desktop mới yêu cầu tối thiểu một `direction` và một phạm vi vì backend không nhận mảng hướng rỗng và `result_scope` là field đơn bắt buộc. Nếu có Chi tiết thì request dùng `result_scope=detail`, nếu chỉ có Tổng quan thì dùng `overview`. Checkbox Hóa đơn/HTML là lựa chọn giao diện chưa có field tương ứng trong create-job; việc tạo HTML thật thuộc Phase 5.

## Phase 2 — account-connections

Renderer không gọi các endpoint tài khoản trực tiếp. Preload chỉ expose bốn command có kiểu:

- `accountConnections.create({ username, password })`;
- `accountConnections.get(connectionId)`;
- `accountConnections.reconnect(connectionId, { username, password })`;
- `accountConnections.revoke(connectionId)`.

Electron main process kiểm tra lại MST, độ dài password và định dạng `connection_id`, gắn header xác thực rồi chỉ trả DTO đã sanitize. Password không được log hoặc trả về renderer. Với development/staging, `MIA_API_BASE_URL` và `MIA_API_ACCESS_TOKEN` được đọc từ process environment; không có biến secret `VITE_*`.

`scripts/account-connections-smoke.mjs` kiểm tra đủ create/get/reconnect/revoke với tài khoản staging chuyên dụng và cố gắng cleanup connection trong `finally`. Script không chạy trong CI mặc định vì cần secret và portal account được phép.

Không dùng `/v1/sessions`: endpoint này đã có header `Deprecation: true` và sunset ngày 2026-12-01.

## Khoảng trống trước khi phát hành EXE

Backend hiện xác thực bằng khóa dịch vụ dài hạn trong `X-MIA-API-Key`. Khóa này không được nhúng vào renderer, preload, source map, `app.asar` hoặc biến `VITE_*`. Trước Phase phát hành cần bổ sung contract kích hoạt thiết bị:

1. Client gửi license key, public key Ed25519 và metadata phiên bản.
2. Server trả challenge dùng một lần.
3. Client ký challenge bằng private key được Windows DPAPI bảo vệ.
4. Server bind license với fingerprint public key và cấp access token ngắn hạn.
5. Server hỗ trợ revoke, giới hạn số thiết bị, refresh token rotation và audit.

Mọi request production nên chạy ở Electron main process; renderer chỉ gọi IPC allowlist và không nhìn thấy token.
