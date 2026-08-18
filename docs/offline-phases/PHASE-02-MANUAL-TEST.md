# Phase 2 — hướng dẫn kiểm chứng tay

## Luồng local account

1. Cài/mở bản Phase 2 trên Windows.
2. Nếu database mới, xác nhận bảng tài khoản hiển thị `0 tài khoản`.
3. Nhấn **Thêm tài khoản**, nhập MST test và password rồi thêm.
4. Quay lại danh sách; xác nhận MST xuất hiện với trạng thái **Chưa kiểm tra đăng nhập**.
5. Đóng/mở app; xác nhận tài khoản vẫn còn.
6. Xóa tài khoản; đóng/mở lại và xác nhận tài khoản không quay lại.
7. Thêm trùng MST; xác nhận không tạo dòng thứ hai.

## Kiểm tra bảo mật

- Không gửi password/MST thật vào ảnh chụp hoặc log báo lỗi.
- Tìm trong `%APPDATA%\mia-desktop-app\offline-runtime\logs`; không được thấy password plaintext.
- Không dùng editor mở/sửa database đang chạy.
- Copy `mia.sqlite3` sang máy Windows khác không đủ để lấy password; kiểm tra giải mã chéo máy chỉ thực hiện bằng test harness được phê duyệt, không nhập password thật vào command.

## Portal verification

Trạng thái này hiện `BLOCKED`: crawler đăng nhập cần CAPTCHA model/template có provenance/license hợp lệ. Không được coi trạng thái `unchecked` là đăng nhập thành công.

Chỉ đánh dấu toàn bộ Phase 2 `PASS` sau khi có bằng chứng portal login thành công, sai mật khẩu, CAPTCHA timeout/retry và account locked trên Windows thật.
