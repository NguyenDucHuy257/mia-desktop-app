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

## Error

Runtime dùng JSON-RPC code chuẩn `-32700`, `-32600`, `-32601`, `-32602`, `-32603` và message ổn định, không trả exception/raw traceback. Electron chuyển lỗi thành code sanitize cho renderer.
