# Phase 3 — hướng dẫn kiểm chứng tay

## Chuẩn bị

1. Cài artifact Windows của PR Phase 3 và mở app.
2. Dùng một tài khoản test local đã tạo ở Phase 2; không ghi password vào log hoặc ảnh chụp.

## Job local lifecycle

1. Mở từng menu và xác nhận có thể chọn/bỏ độc lập Mua vào, Bán ra, Tổng quan, Chi tiết, Hóa đơn, XML, HTML và PDF.
2. Bỏ hết một nhóm rồi nhấn **Đồng bộ dữ liệu**; xác nhận app cảnh báo cần chọn ít nhất một hướng, phạm vi và loại dữ liệu.
3. Chọn lại ít nhất một mục ở mỗi nhóm rồi đồng bộ; xác nhận trạng thái `queued` và tiến trình nằm trong 0–100%.
4. Đóng và mở lại app; xác nhận job `queued` được resume, không xuất hiện job thứ hai.
5. Nhấn **Dừng tải**; job queued phải chuyển thẳng sang `cancelled`.
6. Đóng/mở lại app; job terminal không được tự chạy lại.

## Bảo mật và dữ liệu

- `runtime.log` không được chứa password, token, request body hoặc MST thật.
- Không còn file `%APPDATA%\mia-desktop-app\jobs\active-job.json`; lifecycle nằm trong SQLite.
- Không chỉnh database đang chạy bằng SQLite editor.

## Gate chưa thể nghiệm thu

Job tải hóa đơn nhỏ từ portal thật là `BLOCKED` vì crawler/CAPTCHA model/template chưa vượt gate provenance/license. Không coi trạng thái `queued` là bằng chứng crawler đã chạy.
