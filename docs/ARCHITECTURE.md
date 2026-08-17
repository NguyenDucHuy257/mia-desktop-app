# Kiến trúc MIA Desktop

## Trust boundary

| Lớp | Trách nhiệm | Không được giữ |
|---|---|---|
| React renderer | UI, form, state hiển thị | service key, refresh token, private key, Node API |
| Preload | IPC allowlist có kiểu | logic nghiệp vụ, filesystem tổng quát |
| Electron main | API broker, DPAPI, device signing, file writer | credential portal lâu dài |
| MIA backend | license, token, account connection, crawl/job/result | private key thiết bị |

`contextIsolation`, sandbox và `nodeIntegration: false` là bắt buộc. Main process chỉ nhận IPC từ đúng origin app và validate toàn bộ input.

## Xác minh máy

1. Lần chạy đầu, main process tạo Ed25519 keypair.
2. Private key được mã hóa bằng Electron `safeStorage`/Windows DPAPI; renderer chỉ nhận public key và fingerprint.
3. Backend tạo challenge dùng một lần, có TTL và gắn với activation attempt.
4. Main ký challenge; backend verify chữ ký rồi bind license với public-key fingerprint.
5. Backend cấp access token ngắn hạn và refresh token xoay vòng; token chỉ nằm trong main process và secure storage.
6. Revoke, giới hạn số máy, replay detection và offline-grace là policy phía server.

Các endpoint activation chưa tồn tại trong backend production nên Phase 6 là thay đổi phối hợp giữa hai repo, không phải logic có thể giải quyết riêng trong EXE.

## Luồng job

Renderer gửi command có kiểu qua IPC. Main process thêm token ngắn hạn và `Idempotency-Key`, gọi API, sanitize lỗi rồi trả DTO không chứa secret. Trạng thái job được persist tối thiểu (`job_id`, `connection_id`, intent, timestamp) để tiếp tục poll sau khi app restart. File tải về được ghi atomically vào thư mục do người dùng chọn.
