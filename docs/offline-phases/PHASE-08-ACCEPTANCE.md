# Phase 8 — Acceptance và soak test

Branch: `feat/offline-phase-08-acceptance`

## Luồng nghiệm thu

1. Mở app với danh sách tài khoản trắng.
2. Thêm và xác minh tài khoản.
3. Chọn tùy ý loại dữ liệu và nhấn Đồng bộ.
4. Theo dõi tiến trình, đóng/mở app và xác minh resume.
5. Xem overview/detail theo tài khoản.
6. Tải riêng từ menu ba chấm.
7. Chọn folder và tải toàn bộ Excel/XML/HTML/PDF.
8. Đối soát dữ liệu và artifact.

## Test bắt buộc

- Một và nhiều tài khoản.
- Phạm vi nhỏ và dữ liệu lớn.
- Soak 4–8 giờ, sleep/wake và restart.
- Mạng chập chờn, portal rate-limit, disk gần đầy.
- CPU, RAM, disk, browser và child-process leak.
- Log không chứa MST, password hoặc payload hóa đơn.

## Gate

- Không crash, mất dữ liệu hoặc process leak.
- Kết quả/artifact đối soát đúng.
- Báo cáo cuối phân biệt rõ PASS, FAIL, NOT RUN và BLOCKED.
