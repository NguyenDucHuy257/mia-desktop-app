# Electron–Python runtime protocol v1

Transport là JSON-RPC 2.0 qua `stdin/stdout`, UTF-8, mỗi message kết thúc bằng LF. Không dùng shell, TCP port hoặc HTTP server.

## Giới hạn

- Tối đa 1 MiB cho mỗi request/response, tính theo UTF-8 bytes.
- `id` của request là integer dương do Electron tạo.
- Method dài tối đa 128 ký tự và chỉ chứa chữ, số, `.`, `_`, `-`.
- Electron đặt timeout riêng cho từng request.
- Response sai JSON/protocol hoặc quá kích thước làm runtime bị terminate và toàn bộ pending request bị reject.
- Response đến sau timeout bị bỏ qua theo `id`, không ghi đè request mới.

## Method Phase 0

| Method | Params | Result |
|---|---|---|
| `system.health` | `{}` | protocol/runtime version và PID |
| `system.echo` | JSON object/array | chính payload đầu vào, chỉ dùng kiểm tra boundary |
| `system.sleep` | `{milliseconds: 0..5000}` | thời gian đã sleep, dùng kiểm tra timeout |
| `system.shutdown` | `{}` | `{accepted: true}`, sau đó process thoát |
| `storage.initialize` | `{data_dir: absolute path}` | schema version và integrity |
| `storage.status` | `{}` | schema version và integrity |

Business method sẽ được version hóa và thêm theo từng phase. Credential không được xuất hiện trong response/error.

## Method Phase 3

| Method | Mục đích |
|---|---|
| `jobs.start` | Tạo job idempotent và event `queued` trong cùng transaction |
| `jobs.resume` | Lấy job chưa terminal mới nhất sau khi mở lại app |
| `jobs.status` | Đọc snapshot hiện tại cùng `event_sequence` |
| `jobs.summary` | Đọc lịch sử event có thứ tự |
| `jobs.cancel` | Cancel idempotent; queued/waiting thành cancelled, running thành cancelling |
| `jobs.transition` | Worker nội bộ cập nhật trạng thái/progress bằng optimistic `expected_sequence`; không expose qua renderer IPC |
| `jobs.clear` | Xóa các job terminal; không xóa job đang chạy |

## Method Phase 4

| Method | Mục đích |
|---|---|
| `results.import_overviews` | Worker nội bộ upsert overview theo business key; không expose renderer |
| `results.import_details` | Worker nội bộ upsert detail theo invoice + line key; không expose renderer |
| `results.overview` | Đọc overview bằng opaque keyset cursor, search và direction filter |
| `results.details` | Đọc detail bằng opaque keyset cursor, search và direction filter |

## Error

Runtime dùng JSON-RPC code chuẩn `-32700`, `-32600`, `-32601`, `-32602`, `-32603` và message ổn định, không trả exception/raw traceback. Electron chuyển lỗi thành code sanitize cho renderer.
