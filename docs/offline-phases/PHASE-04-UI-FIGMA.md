# Phase 4-UI — Clone và hoàn thiện giao diện từ Figma

Branch: `feat/offline-phase-04-ui-figma`

Phase này thực hiện ngay sau Phase 4 Results foundation và trước Phase 4A crawler. Mục tiêu là khóa giao diện, component và interaction contract trước khi nối portal thật.

Figma: `https://www.figma.com/design/AvrM4nHgn649ZZeoLei2vN/MIA?node-id=0-1`

## Quy trình Figma bắt buộc

- Dùng kết nối Figma để lấy design context theo từng node; không đoán từ ảnh tổng hoặc tự đo thủ công khi context có sẵn.
- Export asset/icon/font/baseline trực tiếp từ Figma; không tự vẽ lại asset đã tồn tại.
- Lưu manifest node ID → màn hình/component → asset → viewport → baseline checksum.
- Viewport chuẩn 1500×1024, device scale 1, Inter bundle; sidebar cố định 210 px.
- Không dùng screenshot của app làm baseline. Nếu thiếu quyền node/export: `BLOCKED: Figma access`.

## Màn hình phải clone

- Main/account management: `1:466`.
- Thêm tài khoản đơn lẻ/hàng loạt: `1:368`, `60:1182`.
- Option/declaration: `4:628`, `4:654`; điều chỉnh theo quyết định sản phẩm mới nhất nếu menu đã được tách sang tab.
- Results Tổng quan/Chi tiết: `1:466`, `85:16452`.
- XML: `1:654`.
- HTML: `104:22`.
- PDF Downloader/Converting: `106:18856`, `106:19444`.
- Các frame/node còn lại phải được lập inventory từ file Figma, không bỏ sót chỉ vì chưa được liệt kê trong roadmap cũ.

## Chi tiết UI/interaction

- Hoàn thiện sidebar, topbar, account table, company name, checkbox, select-all/indeterminate và menu ba chấm.
- Search có debounce/clear, filter kết hợp, cursor navigation/Tải thêm và trạng thái trang cuối.
- Form validation, password visibility/autocomplete, bulk preview và duplicate feedback.
- Job progress tổng/tháng, cancel, retry, resume, lịch sử và trạng thái terminal.
- Results overview/detail: loading skeleton, empty, partial, error/retry, long text, Unicode và table overflow.
- Artifact tabs: folder picker UI, queue/progress, duplicate, cancel/retry và completion summary.
- Modal notification: error icon đỏ, notice dấu chấm than, nút Đóng, focus trap/restore.
- Dropdown/popover tự đóng khi click-outside hoặc `Esc`; keyboard navigation đầy đủ.
- Không còn placeholder, nút giả hoặc control không có hành vi trong demo adapter.

## Boundary của phase

- Dùng typed demo adapter/fixture đã sanitize để bao phủ toàn bộ trạng thái UI.
- Không copy crawler, model, template hoặc secret trong phase này.
- Không tuyên bố workflow portal PASS; Phase 4A sẽ thay demo adapter bằng runtime crawler thật qua cùng contract.
- Không đổi SQLite/business contract chỉ để làm UI dễ hơn; mọi thay đổi contract phải có migration và test riêng.

## Test bắt buộc

- Visual từng frame ở 1500×1024, mục tiêu ≤1% cho frame mới.
- Layout 1024, 1280, 1366, 1440, 1500 và 1600 px; zoom 125%/150%.
- Default, hover, focus, pressed, disabled, loading, empty, partial, error, retry và success.
- Search nhanh liên tiếp, filter kết hợp, cursor lặp/trang cuối và response đảo thứ tự.
- Multi-select, select-all/indeterminate, menu ba chấm và bulk action bằng demo adapter.
- Keyboard-only, focus trap/restore, accessible name, contrast và reduced motion.
- Fixture/screenshot/log không chứa MST, password, token hoặc payload hóa đơn thật.

## Gate

- Inventory bao phủ 100% frame/node thuộc sản phẩm desktop trong Figma.
- Tất cả màn hình release không còn placeholder và visual đạt gate hoặc có ngoại lệ ghi diff thật được người dùng duyệt.
- Interaction/responsive/accessibility tests PASS.
- Người dùng nghiệm thu installer UI trên Windows.
- Crawler/portal thật vẫn `NOT RUN` trong phase này và không cản nghiệm thu UI; end-to-end được gate ở Phase 4A/4B/6.5.
