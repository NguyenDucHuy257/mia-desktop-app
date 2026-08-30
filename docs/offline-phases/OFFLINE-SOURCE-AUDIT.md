# Kiểm kê nguồn offline — Phase 0

## Mốc kiểm kê

- Backend production: `63acf111c64b47ac964608141b2c83bbb6e2f688`.
- Checkout tham chiếu local khi audit ở `64ebb6ec0a35c784e194e8ce116c4bb4cf1b19d3`; candidate được đối chiếu lại bằng tree commit production.
- Phase 0 không copy file nghiệp vụ nào từ backend; runtime prototype là mã stdlib mới.

## Ma trận quyết định

| Nhóm | Candidate | Quyết định Phase 0 | Điều kiện trước khi nhập |
|---|---|---|---|
| Crawler/session | `app/crawlers`, `portal_session.py` | chưa nhập | portal/rate-limit/redaction test và điều khoản sử dụng |
| CAPTCHA | `app/captcha`, model `.pt` | blocked | xác minh provenance/license model và PyQt/torch packaging |
| Parser | XML/parser/row builder | candidate | fixture đã sanitize và dependency review |
| SQLite repositories | `app/repositories` | tham khảo thiết kế | migration/version/concurrency audit |
| Artifact services | exporters/download/storage | candidate | path safety, atomic write, template license |
| Templates | bốn file `.xlsx` | blocked | xác minh quyền phân phối và provenance |
| FastAPI/API auth | route/client/auth | loại | không thuộc runtime offline |
| Deployment/cloud worker | workflow/script/config | loại | không nhập vào desktop |
| Secret/credential | `.env`, token, portal data | loại tuyệt đối | không có ngoại lệ |

## Dependency/license

`requirements.txt` gồm requests, openpyxl, Pillow, Playwright, PyQt5, torch và torchvision cùng dependency phụ. Metadata môi trường xác nhận một số license permissive, nhưng có điểm chặn:

- `PyQt5` báo GPL v3; cần legal review hoặc thay implementation trước phân phối proprietary desktop.
- License Playwright, torch và torchvision phải được xác minh từ artifact/version thực tế khi khóa runtime.
- Model CAPTCHA `.pt` và template Excel không có provenance/license rõ trong tree đã kiểm tra.
- Không thấy license file gốc ở root trong tree production đã liệt kê.

Do đó chưa được đóng gói CAPTCHA/model/template trong Phase 0.

## Secret audit

Quét marker nhạy cảm tìm thấy module config/session/log có xử lý password/token. Đây là tín hiệu bắt buộc review, không phải bằng chứng file chứa secret thật. Không có file backend nào được chọn/copy trong Phase 0; prototype chỉ nhận allowlisted environment và test chứng minh API token/password không vượt boundary.

## Ước lượng đóng gói

Torch/torchvision, Qt và browser binaries là nhóm chi phối dung lượng. Chưa đưa ra số installer vì artifact đúng version chưa được cài/khóa; Phase 1 phải đo kích thước thực sau khi giải quyết license và chọn cơ chế CAPTCHA/browser.
