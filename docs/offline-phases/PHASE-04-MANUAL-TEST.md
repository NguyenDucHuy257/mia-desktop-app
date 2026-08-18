# Phase 4 — hướng dẫn kiểm chứng tay

## Kết quả local

1. Tạo hoặc chọn một tài khoản local.
2. Nhấn nút ba chấm tại dòng tài khoản và xác nhận màn hình Kết quả hóa đơn mở ra.
3. Chuyển giữa Tổng quan và Chi tiết; kiểm tra loading, empty và nút Thử lại khi runtime lỗi.
4. Nhập tìm kiếm và đổi lọc Mua vào/Bán ra; kết quả phải reset về cursor đầu.
5. Với dữ liệu nhiều hơn 50 dòng, nhấn Tải thêm đến trang cuối; không được trùng hoặc mất dòng.
6. Đóng/mở app và xác nhận dữ liệu SQLite vẫn còn.

## Gate

- Regression tự động 421 dòng: PASS.
- Dữ liệu crawler portal thật: BLOCKED do crawler/CAPTCHA artifacts chưa vượt provenance/license gate.
- Visual Figma `1:466` và `85:16452`: NOT RUN vì chưa có PNG export trực tiếp từ hai node trong repo; không dùng ảnh app làm baseline.
