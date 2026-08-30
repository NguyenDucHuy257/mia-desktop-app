# Phase 4 — Overview và detail cục bộ

Branch: `feat/offline-phase-04-results`

## Phạm vi

- Xây nền lưu trữ/truy vấn/UI results; crawler/parser thật được tích hợp riêng tại [Phase 4A](PHASE-04A-CRAWLER.md).
- Lưu overview/detail trong SQLite.
- Cursor pagination cục bộ, tìm kiếm, lọc, loading, empty, error và retry.
- Chống trùng bằng khóa nghiệp vụ; không mất dòng khi resume hoặc crawl lại.
- Đồng bộ theo lựa chọn mua vào/bán ra và tổng quan/chi tiết độc lập.
- Menu ba chấm xem kết quả riêng theo tài khoản.

## Test bắt buộc

- Dataset 421 items: đủ, không trùng, không thiếu.
- Cursor rỗng, hỏng, lặp và trang cuối.
- Mua vào, bán ra và đồng thời cả hai.
- Tổng quan/chi tiết độc lập.
- Portal thiếu field hoặc thay đổi HTML có kiểm soát.
- Visual Figma `1:466`, `85:16452`, mục tiêu ≤1%.
- Layout 1024, 1280, 1366, 1440 và 1600 px.

## Gate

- Regression 421 items đạt tuyệt đối.
- Kết quả đúng sau restart.
- Visual đạt gate hoặc có ngoại lệ được người dùng duyệt và ghi số diff thật.

## Hiện trạng triển khai

- SQLite schema v3, upsert overview/detail idempotent và opaque keyset cursor.
- Renderer chỉ được đọc overview/detail qua IPC allowlist; import chỉ dành cho Python worker.
- UI mở từ nút ba chấm tài khoản, có Tổng quan/Chi tiết, search, direction filter, loading, empty, error/retry và Tải thêm.
- Regression synthetic 421 items PASS tuyệt đối; crawler portal thật vẫn BLOCKED bởi gate provenance/license.
- Visual Figma hai frame mới NOT RUN cho đến khi có baseline export trực tiếp từ Figma.
- Không coi Phase 4 hoàn chỉnh end-to-end cho tới khi Phase 4A crawler và Phase 4B multi-account đạt gate.
