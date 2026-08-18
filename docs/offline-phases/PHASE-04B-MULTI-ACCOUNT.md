# Phase 4B — Điều phối nhiều tài khoản và vận hành local

Branch: `feat/offline-phase-04b-multi-account`

## Phạm vi

- Checkbox từng tài khoản, select-all/indeterminate và danh sách chọn nhiều tài khoản.
- Một batch cha tạo child job idempotent cho từng tài khoản; không dùng một `connection_id` giả cho cả batch.
- Progress tổng batch, progress từng tài khoản, cancel/retry riêng và cancel/retry toàn bộ.
- Mở lại app resume đúng batch/child job, không nhân đôi.
- Nút ba chấm xem/tải riêng; nút Đồng bộ/Tải xuống luôn áp dụng toàn bộ lựa chọn.
- Tên công ty tự động từ Phase 4A, search theo MST/tên và trạng thái đăng nhập.
- Lịch sử job, log viewer đã redact, retention/cleanup và cấu hình concurrency/retry.

## Test bắt buộc

- 0, 1, 2, 50 tài khoản; select-all, indeterminate và bỏ chọn.
- Một tài khoản lỗi không làm mất kết quả tài khoản khác.
- Batch restart, duplicate command, partial cancel và retry failed-only.
- Giới hạn concurrency, portal rate-limit và bounded backoff.
- Tổng progress luôn 0–100 và không lùi do event cũ.
- Bulk result/artifact không trùng, không thiếu và map đúng tài khoản.

## Gate

- Multi-account chạy bằng crawler thật của Phase 4A.
- Không còn hành vi UI tích nhiều nhưng chỉ chạy tài khoản cuối.
- Batch/child job và kết quả đúng sau restart trên Windows installer.
