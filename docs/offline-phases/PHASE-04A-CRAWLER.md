# Phase 4A — Tích hợp crawler portal offline

Branch: `feat/offline-phase-04a-crawler`

Điều kiện bắt đầu: [Phase 4-UI](PHASE-04-UI-FIGMA.md) đã merge, UI contract và typed demo adapter đã được khóa.

Nguồn chuẩn: `https://github.com/hvsoftware26/mia-crawl-service`, phải pin exact commit trước khi nhập. Không theo branch trôi nổi.

## Chính sách bê nguyên source

- Module crawler, portal session, parser và service nghiệp vụ đủ điều kiện được copy vào thư mục vendor, giữ nguyên nội dung byte-for-byte.
- Sinh manifest gồm source path, source commit và SHA-256; CI fail nếu file vendor bị sửa trực tiếp.
- Adapter Electron/Python offline viết ở ngoài vendor để cung cấp credential, cancel signal, progress callback, SQLite repository và artifact path.
- Không copy FastAPI routes/server, HTTP authentication, PostgreSQL adapter, cloud worker, deployment, `.env`, credential hoặc dữ liệu portal thật.
- CAPTCHA model, Excel template và dependency chỉ được đóng gói sau khi provenance/license và quyền phân phối được xác nhận.
- Không thể gọi là “copy 100% repository”: bỏ API đồng nghĩa phải loại các thành phần server. Mục tiêu là giữ nguyên 100% module nghiệp vụ được chọn.

## Luồng bắt buộc

1. Electron main giải mã password bằng `safeStorage` đúng lúc chạy; renderer không nhận password.
2. Python worker đăng nhập portal, giải CAPTCHA, lấy tên công ty và cập nhật trạng thái tài khoản.
3. Crawler chạy theo mua vào/bán ra và tổng quan/chi tiết đã chọn.
4. Worker cập nhật `jobs.transition` và ghi overview/detail qua transaction SQLite.
5. Cancel/restart tiếp tục từ checkpoint, không crawl lại phần đã hoàn tất và không nhân đôi dữ liệu.
6. Cookie/session/password chỉ tồn tại trong memory cần thiết, không lưu SQLite hoặc log.

## Test bắt buộc

- Manifest/hash chứng minh vendor source không bị sửa.
- Chạy lại toàn bộ backend unit test áp dụng được cho crawler/parser/service.
- Login đúng, sai mật khẩu, locked, CAPTCHA sai/timeout/retry và portal unavailable.
- HTML/JSON thiếu field, schema thay đổi, rate-limit, session expiry và network recovery.
- Tên công ty được lấy và persist đúng theo MST.
- Small job thật cho mua vào, bán ra, overview và detail; đối soát số lượng với portal.
- Cancel, crash, restart/resume và idempotency trên dữ liệu thật.
- Log/crash dump không chứa MST, password, cookie, token hoặc payload hóa đơn.

## Gate

- `BLOCKED: source license` nếu chủ sở hữu chưa xác nhận quyền tái sử dụng/phân phối source.
- `BLOCKED: CAPTCHA/model/template provenance` cho tới khi có bằng chứng quyền phân phối.
- Không thay CAPTCHA bằng mock để tuyên bố workflow thật PASS.
- Chỉ hoàn thành khi small-job portal thật đi hết account → crawler → SQLite → Results UI mà không gọi business HTTP API.
