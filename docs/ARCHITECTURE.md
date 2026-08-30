# Kiến trúc MIA Desktop offline

## Trust boundary mục tiêu

| Lớp | Trách nhiệm | Không được giữ/thực hiện |
|---|---|---|
| React renderer | UI, form và trạng thái hiển thị | Node API, filesystem, crawler, password plaintext |
| Preload | IPC allowlist có kiểu | business logic hoặc bridge tổng quát |
| Electron main | xác minh sender, validate DTO, DPAPI, file dialog/writer, điều phối runtime | crawler trực tiếp hoặc log credential |
| Python child process | crawler, parser, SQLite và artifact engine | UI, Electron API, deployment secret |
| SQLite/user data | trạng thái job và dữ liệu hóa đơn local | password plaintext |

`contextIsolation: true`, `sandbox: true`, `nodeIntegration: false` và xác minh IPC sender tiếp tục là gate bắt buộc.

## Biên Electron ↔ Python

Electron sinh một Python child process không qua shell, ẩn cửa sổ trên Windows và dùng JSON-RPC 2.0 dạng một JSON object trên mỗi dòng. `stdout` chỉ dành cho protocol; diagnostic dùng `stderr` và không được chứa dữ liệu nhạy cảm. Giới hạn message là 1 MiB.

Child process chỉ nhận tập biến môi trường cho phép; token/password/API key trong môi trường Electron không tự động truyền sang Python. Electron quản lý timeout, pending request, protocol violation và shutdown. Xem [protocol chi tiết](offline-phases/OFFLINE-RUNTIME-PROTOCOL.md).

Phase 0 chỉ có runtime stdlib phục vụ health/echo/timeout/shutdown để chứng minh boundary. Crawler, CAPTCHA/model và thư viện bên thứ ba chỉ được nhập ở phase sau khi audit.

## Dữ liệu local

SQLite thay PostgreSQL. Schema mục tiêu có migration version và transaction cho account, job, event/progress, overview, detail, artifact và setting. Password được mã hóa bằng Electron `safeStorage`/Windows DPAPI trước khi lưu; SQLite chỉ chứa ciphertext. Xem [schema đề xuất](offline-phases/OFFLINE-SQLITE-SCHEMA.md).

## Vòng đời

1. Electron ready và tạo một runtime process.
2. Main gọi `system.health`, kiểm tra protocol version.
3. Renderer chỉ gửi command qua preload allowlist.
4. Main validate, chuyển command cần thiết sang runtime và sanitize kết quả.
5. Khi đóng app, main dừng nhận request, gửi `system.shutdown`, chờ có giới hạn rồi kill nếu cần.
6. Job bền vững được resume từ SQLite ở Phase 3.

## Phần kiến trúc API cũ

Các client/broker HTTP hiện có vẫn tồn tại để bảo toàn baseline trong giai đoạn chuyển đổi nhưng không còn là kiến trúc mục tiêu. Chúng chỉ được xóa sau khi các lát cắt offline tương ứng đã có test thay thế và migration an toàn.
