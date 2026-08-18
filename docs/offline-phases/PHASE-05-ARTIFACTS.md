# Phase 5 — XML, HTML, PDF và Excel

Branch: `feat/offline-phase-05-artifacts`

## Phạm vi

- Tái sử dụng artifact module đã qua audit, chạy trong Python process.
- Chọn thư mục và ghi file qua Electron main; renderer không truy cập filesystem.
- Dùng một ô đường dẫn chung dựa trên UI tab PDF.
- Menu ba chấm tải riêng từng tài khoản.
- “Tải xuống luôn” xuất toàn bộ lựa chọn, gồm Excel overview/detail và mua vào/bán ra.
- Ghi atomically, xử lý tên trùng, progress, cancel và retry.

## Test bắt buộc

- XML/HTML/PDF mở được và Excel đúng dòng/cột.
- Unicode tiếng Việt và chống formula injection trong Excel.
- Path traversal, absolute path, Windows reserved names và thoát thư mục.
- File trùng, hỏng, tải thiếu, hết dung lượng và mất quyền ghi.
- Cancel không để file hoàn chỉnh giả; cleanup file tạm.
- Tải riêng một tài khoản và tải tất cả nhiều tài khoản.
- Visual Figma `1:654`, `104:22`, `106:18856`, `106:19444`.

## Gate

- Artifact integrity đạt toàn bộ test.
- Không có filesystem access từ renderer.
- Không ghi ra ngoài thư mục người dùng chọn.
