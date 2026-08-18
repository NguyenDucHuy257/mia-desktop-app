# Phase 6.5 — Hoàn thiện toàn bộ UI/UX

Branch: `feat/offline-phase-06-5-full-ui`

Phase này thực hiện sau Phase 5 và các phần crawler/results cần thiết, trước Phase 7 release. Không dùng mock để tuyên bố hoàn thành luồng nghiệp vụ.

## Phạm vi màn hình

- Trạng thái lần đầu mở app, thêm đơn lẻ/hàng loạt, sửa, xóa và chọn nhiều tài khoản.
- Quản lý hóa đơn, đồng bộ, tiến trình hai cấp, cancel/retry/resume và lịch sử job.
- Kết quả Tổng quan/Chi tiết theo từng tài khoản và toàn bộ tài khoản.
- Các tab XML, HTML, PDF, Excel/MVT, Nhật ký, Cài đặt và chọn thư mục đầu ra.
- Menu ba chấm, dropdown, modal thông báo, confirm hành động nguy hiểm và tooltip.
- Mọi control có default, hover, focus-visible, pressed, disabled, loading, empty, error và success.

## Search, filter và dữ liệu bảng

- Search có debounce, nút xóa nhanh, không để response cũ ghi đè query mới.
- Filter Mua vào/Bán ra, khoảng ngày, trạng thái, tài khoản và loại dữ liệu có thể kết hợp.
- Cursor keyset giữ nguyên; không giả page-number khi backend/runtime không có page-number.
- Tải thêm/previous history chống cursor lặp, dòng trùng và mất dòng; reset cursor đúng khi search/filter đổi.
- Sort chỉ bật cho field được SQLite/runtime hỗ trợ và có thứ tự ổn định.
- Multi-select, select-all theo tập dữ liệu đang hiển thị, indeterminate state và bulk action rõ ràng.
- Giữ search/filter/tab/scroll hợp lý khi quay lại; không giữ dữ liệu lỗi thời giữa hai tài khoản.

## Responsive và accessibility

- Kiểm tra 1024, 1280, 1366, 1440, 1500 và 1600 px; sidebar giữ 210 px.
- Bảng rộng dùng overflow có chủ đích, header ổn định và không làm nút/chữ xuống dòng ngoài thiết kế.
- Điều hướng hoàn toàn bằng bàn phím; `Esc` đóng menu/modal, click-outside đóng popover.
- Focus trap cho modal, trả focus về trigger, semantic roles/labels và thông báo screen reader.
- Contrast, zoom 125%/150%, text dài, tên công ty dài và dữ liệu Unicode tiếng Việt.

## Figma

- Đối chiếu từng node qua design context, không đoán từ ảnh toàn màn hình.
- Dùng asset export trực tiếp; không tự vẽ lại asset đã có.
- Các node tối thiểu: main `1:466`, account `1:368`/`60:1182`, option `4:628`/`4:654`, detail `85:16452`, XML `1:654`, HTML `104:22`, PDF `106:18856`/`106:19444`.
- Baseline phải là PNG export từ Figma ở 1500×1024, scale 1; không cập nhật bằng ảnh app.
- Mỗi frame mới mục tiêu diff ≤1%; ngoại lệ phải ghi số diff thật và được người dùng duyệt.

## Test bắt buộc

- Component/interaction test cho mọi control và trạng thái kể trên.
- Search nhanh liên tiếp, filter kết hợp, response đảo thứ tự và retry.
- Dataset 0, 1, 50, 51, 421 và dữ liệu lớn; không trùng/thiếu qua toàn bộ cursor.
- Multi-account, multi-select, select-all/indeterminate và bulk action.
- Visual từng màn hình và trạng thái tại viewport chuẩn; layout smoke ở toàn bộ width yêu cầu.
- Keyboard-only, focus trap/restore, accessible name và reduced motion.
- Screenshot/log/fixture không chứa MST, password, token hoặc payload hóa đơn thật.

## Gate

- Không còn placeholder hoặc control không hoạt động trong phạm vi sản phẩm release.
- Toàn bộ luồng từ tài khoản → đồng bộ → kết quả → tải file chạy bằng runtime thật.
- Visual đạt gate; interaction, responsive và accessibility test PASS.
- Windows manual test PASS trên installer mới; không merge nếu chỉ test bằng Vite browser adapter.

## Blocker có thể xảy ra

- `BLOCKED: Figma export/access` nếu chưa lấy được design context và PNG baseline trực tiếp.
- `BLOCKED: crawler/runtime` nếu màn hình chưa có dữ liệu thật do crawler/CAPTCHA chưa tích hợp.
- Không hạ gate hoặc dùng mock để đổi hai blocker này thành PASS.
