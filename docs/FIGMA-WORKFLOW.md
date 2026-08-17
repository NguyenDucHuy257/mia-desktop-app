# Quy trình Figma → MIA Desktop

Không thể bảo đảm “100%” trên mọi màn hình/OS vì anti-aliasing, scale DPI và font renderer khác nhau. Tiêu chí có thể kiểm chứng là khớp pixel tại đúng môi trường chuẩn: Electron Chromium đã khóa phiên bản, viewport content 1500×1024, device scale 1 và Inter được bundle trong app.

| Frame | Trạng thái | Gate |
|---|---|---|
| `1:2` | Quản lý HĐĐT | diff pixel ≤3% |
| `1:368` | Thêm tài khoản đơn lẻ | diff pixel ≤1% |
| `60:1182` | Thêm tài khoản hàng loạt | diff pixel ≤1% |

Ở lần đo Phase 2 ngày 2026-08-17, hai frame tài khoản lần lượt sai khác khoảng 0,60% và 0,64% tại threshold màu 0.25. Phần chênh tập trung ở anti-alias font/icon, không phải sai khối layout.

Quy trình cho mỗi frame:

1. Lấy `get_design_context` theo node cụ thể, không dùng screenshot tổng để đoán CSS.
2. Lưu PNG reference và asset thật từ Figma; không tự vẽ lại icon.
3. Map màu, font, spacing, radius vào CSS tokens dùng chung.
4. Tách component dùng lại: Sidebar, Topbar, Toolbar, Table, StatusBadge, ProgressBar.
5. Chụp app tại 1500×1024 và so pixel với Figma trong Playwright.
6. Sửa theo diff overlay; chỉ chấp nhận khi đạt ngưỡng của phase.
7. Chạy lại ở 1024, 1280, 1366, 1440 và 1600 px để phát hiện vỡ layout.

Figma phải có đầy đủ default/hover/pressed/disabled/error/loading/empty/success và modal states. Một ảnh tĩnh không đủ để suy ra chính xác mọi tương tác.
