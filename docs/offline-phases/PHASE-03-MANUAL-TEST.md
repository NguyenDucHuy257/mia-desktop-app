# Phase 3 — hướng dẫn kiểm chứng tay

Trạng thái: **PASS** — người dùng nghiệm thu trên Windows ngày 2026-08-18. Gate portal thật ở cuối tài liệu vẫn `BLOCKED` độc lập.

## Chuẩn bị

1. Cài artifact Windows của PR Phase 3 và mở app.
2. Dùng một tài khoản test local đã tạo ở Phase 2; không ghi password vào log hoặc ảnh chụp.

## Job local lifecycle

1. Xác nhận tab Hóa đơn không còn menu chọn Hóa đơn/XML/HTML/PDF; các loại artifact nằm ở tab riêng.
2. Mở từng menu còn lại và xác nhận có thể chọn/bỏ độc lập Mua vào, Bán ra, Tổng quan và Chi tiết.
3. Khi một menu đang mở, click ra vùng trống hoặc nhấn `Esc`; xác nhận menu tự đóng.
4. Bỏ hết một nhóm rồi nhấn **Đồng bộ dữ liệu**; xác nhận modal có dấu chấm than, nội dung cảnh báo và nút **Đóng**.
5. Với một lỗi job, xác nhận modal nằm giữa màn hình và có biểu tượng lỗi màu đỏ cùng nút **Đóng**.
6. Tại bảng MST, click checkbox tài khoản nhiều lần; xác nhận có thể tích và bỏ tích.
7. Xác nhận nút **Thêm tài khoản** luôn nằm trên một dòng.
8. Chọn lại ít nhất một mục ở mỗi nhóm rồi đồng bộ; xác nhận trạng thái `queued` và tiến trình nằm trong 0–100%.
9. Đóng và mở lại app; xác nhận job `queued` được resume, không xuất hiện job thứ hai.
10. Nhấn **Dừng tải**; job queued phải chuyển thẳng sang `cancelled`.
11. Đóng/mở lại app; job terminal không được tự chạy lại.

## Bảo mật và dữ liệu

- `runtime.log` không được chứa password, token, request body hoặc MST thật.
- Không còn file `%APPDATA%\mia-desktop-app\jobs\active-job.json`; lifecycle nằm trong SQLite.
- Không chỉnh database đang chạy bằng SQLite editor.

## Gate chưa thể nghiệm thu

Job tải hóa đơn nhỏ từ portal thật là `BLOCKED` vì crawler/CAPTCHA model/template chưa vượt gate provenance/license. Không coi trạng thái `queued` là bằng chứng crawler đã chạy.
