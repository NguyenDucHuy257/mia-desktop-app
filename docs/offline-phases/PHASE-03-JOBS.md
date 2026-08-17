# Phase 3 — Job lifecycle offline

Branch: `feat/offline-phase-03-job-lifecycle`

## Phạm vi

- Tạo và lưu job trong SQLite với idempotency cục bộ.
- Resume sau khi mở lại app; cancel/retry có giới hạn.
- Hỗ trợ `queued`, `waiting_account`, `running`, `cancelling`, `completed`, `completed_with_warning`, `failed`, `cancelled`, `abandoned`.
- Hiển thị tiến trình tổng và tháng hiện tại.
- Cho phép chọn/bỏ tự do mua vào, bán ra, tổng quan, chi tiết, XML, HTML và PDF.
- Job không có lựa chọn phải cảnh báo rõ trước khi chạy.

## Test bắt buộc

- Đầy đủ transition state machine.
- Cùng idempotency key không tạo job trùng.
- Restart resume đúng job.
- Response/progress cũ không ghi đè trạng thái mới.
- Progress luôn trong 0–100.
- Cancel ở queued/running/cancelling/terminal.
- Mất mạng tạm thời, crash app và retry.
- Interaction tạo, chạy, hủy, lỗi và phục hồi.

## Gate

- Small-job thật hoàn tất không cần HTTP API.
- Restart không mất hoặc nhân đôi job.
