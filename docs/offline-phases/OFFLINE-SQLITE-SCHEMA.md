# SQLite schema mục tiêu

Phase 0 chỉ chốt schema; implementation và migrations thuộc Phase 1.

## Bảng

| Bảng | Mục đích | Khóa/ràng buộc chính |
|---|---|---|
| `schema_migrations` | version migration đã áp dụng | `version` PK, checksum |
| `accounts` | định danh tài khoản và credential đã mã hóa | UUID PK, unique normalized tax code |
| `jobs` | intent, idempotency và trạng thái | UUID PK, unique idempotency key, status check |
| `job_events` | lịch sử transition/progress append-only | `(job_id, sequence)` unique |
| `invoice_overviews` | dữ liệu tổng quan | unique business invoice key + direction |
| `invoice_details` | dòng chi tiết | unique invoice key + line identity |
| `artifacts` | file XML/HTML/PDF/Excel | job/account/type/path, size/checksum/status |
| `settings` | cấu hình không nhạy cảm | key PK |

## Quy tắc

- Bật foreign keys, WAL và `busy_timeout`.
- Mọi transition job và event tương ứng nằm trong cùng transaction.
- Thời gian lưu UTC ISO-8601.
- Cursor dựa trên immutable keyset, không dựa offset cho luồng lớn.
- Không lưu password plaintext, browser cookie/session hoặc raw secret trong database.
- Artifact path phải nằm dưới thư mục người dùng chọn và được main process kiểm tra lại.
- Migration chỉ tiến tới; backup trước migration phá vỡ tương thích.
