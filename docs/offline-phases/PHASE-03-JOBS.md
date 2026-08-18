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

## Hiện trạng triển khai

- SQLite schema v2 lưu snapshot job và event sequence; create và event đầu tiên nằm trong cùng transaction.
- Electron broker chỉ gọi Python JSON-RPC, không còn dùng HTTP API hay file `active-job.json`.
- `jobs.transition` dành riêng cho worker Python, dùng optimistic sequence để từ chối cập nhật cũ; renderer không được gọi method này.
- Idempotency key là SHA-256 của intent đã chuẩn hóa và chỉ được trao đổi giữa Electron main với runtime.
- Thông báo dùng modal đồng bộ giao diện: lỗi có biểu tượng đỏ ở giữa, cảnh báo/thông tin có dấu chấm than và nút Đóng.
- Menu lựa chọn tự đóng khi click ra ngoài hoặc nhấn `Esc`.
- Runtime lifecycle và UI interaction có thể kiểm thử ngay. Gate tải hóa đơn thật vẫn `BLOCKED` cho đến khi crawler/CAPTCHA artifacts vượt kiểm tra provenance/license.

## Kiểm chứng tay

Thực hiện [PHASE-03-MANUAL-TEST.md](PHASE-03-MANUAL-TEST.md). Không dùng MST/password thật trong log, ảnh chụp hoặc command.
