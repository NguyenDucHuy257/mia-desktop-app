# Phase 2 — Tài khoản cục bộ

Branch: `feat/offline-phase-02-accounts`

## Phạm vi

- UI ban đầu không có tài khoản; hỗ trợ thêm, sửa, xóa và kiểm tra.
- Mã hóa password bằng Electron `safeStorage`/Windows DPAPI.
- Python chỉ nhận credential trong bộ nhớ lúc chạy tác vụ.
- Hiển thị trạng thái chưa kiểm tra, đang kiểm tra, hợp lệ, sai mật khẩu, CAPTCHA lỗi, portal lỗi và bị khóa.
- Menu ba chấm cho từng tài khoản.
- Bảng tài khoản hiển thị cột MST và cột Tên công ty riêng biệt; bỏ cột Kỳ tải. Tên công ty để trống (`—`) cho đến phase tra cứu tự động.

## Test bắt buộc

- MST/password validation, duplicate và giới hạn bulk.
- Encrypt/decrypt và database clone sang máy khác.
- Renderer không đọc được password.
- Log/crash output không chứa credential.
- Portal login thành công, thất bại, CAPTCHA timeout và retry.
- Interaction từ màn hình trắng đến khi thêm tài khoản thành công.

## Gate

- Không có credential plaintext trên disk hoặc trong log.
- Tài khoản test đăng nhập portal thành công trên Windows thật.

## Kiểm chứng tay

Thực hiện [PHASE-02-MANUAL-TEST.md](PHASE-02-MANUAL-TEST.md). Local CRUD và encrypted-at-rest có thể nghiệm thu độc lập; portal verification vẫn là gate bắt buộc, không được thay bằng mock.
