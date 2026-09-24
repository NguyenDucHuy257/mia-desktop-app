# Prompt sửa lõi crawl GDT cho dự án API

Đặc tả được rút từ mã nguồn workspace `mia-desktop-app` ngày 21/09/2026, theo nhánh production đang nối dây trong code. Đây là đối chiếu mã nguồn, không phải kết quả kiểm tra trực tiếp cổng thuế tại thời điểm đọc. Có thể copy toàn bộ phần dưới và gửi cho agent đang làm việc trong dự án API đích; agent không cần có repository desktop.

---

Bạn đang làm việc trong một dự án dạng API đã có chức năng crawl cổng `https://hoadondientu.gdt.gov.vn`. Hãy đọc kỹ code hiện tại, rà soát và trực tiếp sửa lõi gọi HTTP tới cổng thuế để khớp đặc tả tham chiếu bên dưới.

## 1. Phạm vi sửa

Chỉ sửa các phần thực sự liên quan crawl upstream: URL/method, query/body, header, xác thực với cổng thuế, token/session, phân trang, chia khoảng ngày, retry/timeout, đọc response và truyền kết quả/lỗi về cơ chế hiện có.

Giữ nguyên kiến trúc API của dự án: public routes, request/response DTO, xác thực người dùng API, tenant isolation, queue, database, job lifecycle và storage contracts. Chỉ điều chỉnh adapter/call-site tối thiểu nếu cần để tích hợp crawl đúng. Không bê Electron, IPC, UI, licensing/key-server, SQLite desktop, xuất báo cáo hay bộ điều phối desktop sang dự án này. Không thêm một crawler song song rồi để public API tiếp tục gọi crawler cũ.

Trước khi sửa, lần theo public endpoint → service/job/worker → crawler → HTTP client thực sự được sử dụng. Phân biệt code chạy thật, defaults của class, cấu hình override tại call-site và code legacy. Sau khi sửa, xác nhận public API/worker hiện tại đã gọi đúng lõi mới.

## 2. Địa chỉ và mapping bắt buộc

```text
PORTAL_ROOT = https://hoadondientu.gdt.gov.vn/
API_BASE    = https://hoadondientu.gdt.gov.vn/api
LOOKUP_URL  = https://hoadondientu.gdt.gov.vn/tra-cuu/tra-cuu-hoa-don

electronic    -> query
cash_register -> sco-query
purchase      -> mua vào
sold          -> bán ra
```

Chỉ chấp nhận đúng các giá trị trên. Không đổi `sold` thành `sale`, không đổi `sco-query` thành `query`. Cả hai loại hóa đơn đều có cả hai hướng.

| Chức năng | Method | Path tương đối với API_BASE |
|---|---|---|
| CAPTCHA | GET | `/captcha` |
| Đăng nhập | POST | `/security-taxpayer/authenticate` |
| Thông tin doanh nghiệp | GET | `/security-taxpayer/profile` |
| Danh sách | GET | `/{query_type}/invoices/{direction}` |
| Chi tiết | GET | `/{query_type}/invoices/detail` |
| ZIP chứa XML/HTML | GET | `/{query_type}/invoices/export-xml` |
| Excel upstream, nếu dự án có dùng | GET | `/{query_type}/invoices/export-excel` cho sold |
| Excel upstream, nếu dự án có dùng | GET | `/{query_type}/invoices/export-excel-sold` cho purchase |

Không suy luận hướng mua/bán từ tên `export-excel-sold`: endpoint tên này được code tham chiếu dùng cho **purchase**.

## 3. HTTP client và headers

Dùng HTTP session có connection pooling. CAPTCHA và POST login của một lần xác thực dùng chung client/session và cùng route direct/proxy. Luồng managed có thể tạo HTTP session mới cho từng đơn vị crawl nhưng tái sử dụng token được quản lý; không bắt buộc một cookie jar duy nhất sống suốt toàn job.

Headers nền của reference:

```text
accept: application/json, text/plain, */*
accept-language: vi
referer: https://hoadondientu.gdt.gov.vn/tra-cuu/tra-cuu-hoa-don
end-point: /tra-cuu/tra-cuu-hoa-don
request-id: <UUID v4 mới cho mỗi lần gửi HTTP thực tế>
sec-ch-ua: "Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Windows"
sec-fetch-dest: empty
sec-fetch-mode: cors
sec-fetch-site: same-origin
user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36
authorization: Bearer <source_token>
```

Chỉ thêm Authorization khi đã có token. Token này là token cổng thuế, không phải token xác thực public API hoặc license. Không hardcode token/cookie/request-id lấy từ một phiên mẫu.

Mỗi lần retry phải sinh request-id mới, kể cả các lần retry cùng URL và params. Xử lý tên header không phân biệt hoa thường: khi ghi đè phải loại header cũ cùng tên, không để hai Authorization/Action/End-Point khác casing.

`Action` dùng percent-encoding UTF-8 tương đương Python `urllib.parse.quote(text, safe='()')`: dấu cách thành `%20`, giữ `(` và `)`, không dùng `+`, không encode hai lần.

| Request | Text gốc của Action trước encode |
|---|---|
| List sold | `Tìm kiếm (hóa đơn bán ra)` |
| List purchase | `Tìm kiếm (hóa đơn mua vào)` |
| Detail sold | `Xem hóa đơn (hóa đơn bán ra)` |
| Detail purchase | `Xem hóa đơn (hóa đơn mua vào)` |
| Package XML sold | `Xuất xml (hóa đơn bán ra)` |
| Package XML purchase | `Xuất xml (hóa đơn mua vào)` |
| Package HTML sold | `In hóa đơn (hóa đơn bán ra)` |
| Package HTML purchase | `In hóa đơn (hóa đơn mua vào)` |

Detail dùng **Xem hóa đơn**, không dùng **In hóa đơn**. List, detail và package dùng Referer/End-Point của màn tra cứu như trên.

## 4. Xác thực upstream

Một lần xác thực thực hiện:

1. `GET /captcha`, không query/body, chưa có Authorization. Header context đổi thành `referer: PORTAL_ROOT`, `end-point: /`, `action: ""`.
2. Đọc JSON lấy `key` và `content`; thiếu một trong hai là lỗi. Đưa `content` vào bộ giải CAPTCHA hiện có để lấy giá trị trả lời. Không thay cơ chế giải CAPTCHA nếu không cần thiết.
3. `POST /security-taxpayer/authenticate`, gửi **JSON**, không form-urlencoded, với đúng bốn field:

```json
{
  "username": "<tài khoản cổng thuế>",
  "password": "<mật khẩu cổng thuế>",
  "cvalue": "<kết quả giải content>",
  "ckey": "<key của CAPTCHA vừa lấy>"
}
```

POST dùng cùng root context như CAPTCHA; HTTP library đặt Content-Type JSON. Đọc field `token` ở cấp gốc response, không tự đổi thành `access_token`.

Mỗi lần authenticate chỉ lấy một CAPTCHA và gửi một POST login. Tham số `attempts` còn xuất hiện ở reference nhưng không tạo vòng lặp POST login. Không tự động lặp login nhiều lần vì tên tham số/default cũ.

`GET /security-taxpayer/profile` nếu cần lấy tên doanh nghiệp: không params/body, có Bearer token, vẫn dùng root context `referer: PORTAL_ROOT`, `end-point: /`, `action: ""`. Đọc field `name`. Đây là bước lấy thông tin tài khoản khi cần, không phải request bắt buộc trước từng trang hóa đơn.

Phân loại lỗi POST login HTTP 401 theo `message` JSON, chuẩn hóa Unicode NFC, khoảng trắng và so sánh không phân biệt hoa thường:

- Chứa `Tên đăng nhập hoặc mật khẩu không đúng` → `invalid_source_credentials`.
- Chứa `Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định` → `source_account_locked`.
- 401 còn lại → `source_login_rejected`.
- Thiếu token → `source_token_missing`.
- Login 429 hoặc HTTP >=500 → lỗi upstream retryable (`source_rate_limited` hoặc `source_http_<status>`), gợi ý chờ 20 giây cho cơ chế job hiện có; không tự loop POST bên trong AuthCrawler.

Không nhầm 401 do đăng nhập sai với 401 khi token bị upstream từ chối trên API dữ liệu. Không log password, source token, CAPTCHA hoặc raw login response.

## 5. Chia ngày và gọi danh sách

Chia khoảng ngày đầu vào thành từng **tháng lịch**, giữ đủ cả ngày đầu và ngày cuối, không chia cố định 30 ngày. Ví dụ `20/01/2026..05/03/2026` thành `20/01..31/01`, `01/02..28/02`, `01/03..05/03`.

Với mỗi hướng/loại/tháng cần crawl:

- `query`: lần lượt ba partition trạng thái `ttxly==5`, `ttxly==6`, `ttxly==8`, áp dụng cho cả purchase và sold.
- `sco-query`: một partition all, **không gửi điều kiện ttxly**.
- Mỗi partition có cursor, total và checkpoint riêng. Không dùng chung cursor giữa tháng, direction, query_type hay status.

Request list:

```text
GET /{query_type}/invoices/{direction}
sort   = tdlap:desc
size   = "50"   # hoặc "30" / "15" khi fallback
search = tdlap=ge=DD/MM/YYYYT00:00:00;tdlap=le=DD/MM/YYYYT23:59:59[;ttxly==N]
state  = <cursor nguyên vẹn từ response trước; bỏ field ở trang đầu>
```

Ví dụ điện tử mua vào, partition 5 tháng 8/2026:

```json
{
  "sort": "tdlap:desc",
  "size": "50",
  "search": "tdlap=ge=01/08/2026T00:00:00;tdlap=le=31/08/2026T23:59:59;ttxly==5"
}
```

Ví dụ máy tính tiền bán ra cùng tháng:

```json
{
  "sort": "tdlap:desc",
  "size": "50",
  "search": "tdlap=ge=01/08/2026T00:00:00;tdlap=le=31/08/2026T23:59:59"
}
```

Dùng query encoder của HTTP client, không nối URL rồi encode lặp. Không thêm page/offset, `type=purchase`, hoặc invoice-id vào request list. `ttxly` là điều kiện trong `search`, không phải một query parameter độc lập. Không nhầm `ttxly` với field trạng thái hóa đơn `tthai`.

Response list đọc `datas`, `total`, `state`. `datas` phải là list, reference dùng `[]` nếu thiếu. `total` đầu tiên parse integer không âm, fallback số dòng trang nếu không parse được.

Phân trang production dùng cursor:

- Nhận trang thành công → lưu kết quả cùng next_state qua cơ chế checkpoint hiện có → mới chuyển cursor.
- Retry lỗi phải giữ nguyên cursor và partition; giảm size không được nhảy cursor hoặc quay lại đầu tùy tiện.
- Mỗi trang mới bắt đầu lại lịch size từ 50.
- Dừng khi response `state` là null hoặc thiếu. Nhánh adaptive reference kiểm tra `None`, không dùng số dòng <size để kết luận đã hết.
- Có cursor tiếp theo thì tiếp tục dù `datas` rỗng/ngắn hoặc số dòng đã chạm total. Kiểm tra cursor lặp để tránh vòng lặp vô hạn; giới hạn 10.000 trang mỗi partition/range.
- Cursor hết nhưng fetched != total: giữ dữ liệu đã nhận, trả cảnh báo `source_total_mismatch`/`completed_with_warning`, gồm expected_total, fetched_count, missing_count. Không giả lập phần thiếu hoặc xóa dữ liệu đã crawl.
- Hết retry lỗi thực sự thì giữ checkpoint partial, không đánh dấu đã lấy đủ.

Reference có preflight tính total: gọi chính list endpoint, cùng params và lịch fallback, không có endpoint count riêng. Chỉ tái sử dụng payload làm trang đầu nếu preflight thành công với size đầu tiên 50. Nếu preflight chỉ thành công ở 30/15 thì dùng total, crawler chính lấy lại trang đầu bằng lịch riêng. Preflight fallback cho timeout/connection error, không mặc nhiên retry mọi HTTP error như lỗi mạng.

## 6. Timeout, retry và phiên: theo nhánh production

Timeout ghi dưới dạng `(connect_seconds, read_seconds)`:

| Loại | Timeout | Retry thông thường |
|---|---|---|
| CAPTCHA/login, client mặc định | `(15,75)` | một GET CAPTCHA + một POST cho mỗi authenticate |
| List/preflight size 50 | `(2,4)` | tối đa 2 attempt ở size này |
| List/preflight size 30 | `(2,4)` | tối đa 2 attempt ở size này |
| List/preflight size 15 | `(2,3)` | tối đa 2 attempt ở size này |
| Detail | `(2,8)` | tối đa 5 attempt |
| ZIP XML/HTML | `(2,20)` | tối đa 2 attempt |
| Excel upstream nếu được gọi | `(15,120)` | electronic 5, cash_register 2 attempt |

Số attempt tính cả lần đầu. List gọi session với `retry_attempts=1` để crawler tự điều khiển fallback size, tránh nhân chồng retry. Delay retry cùng size: `min(0.2 * số_lỗi_tại_size, 0.5)` giây.

Đối với managed GET detail/package, session retry timeout, connection error và HTTP 500/502/503/504. Backoff thường `min(1.5 * 2^(normal_failures-1), 8)` giây. HTTP 401 thuộc nhánh refresh riêng; lỗi HTTP khác được đẩy lên caller, không đăng nhập lại bừa bãi.

HTTP 429 trên **managed GET đang chạy thực tế**:

- Giữ nguyên token, route direct/proxy, URL, params/cursor.
- Chờ lần lượt `2,5,10,20,40,60,80,100,120,140` giây; từ lần tiếp theo giữ 140 giây. Hàm có trần 300 giây cho lịch cấu hình khác.
- Tiếp tục đến khi thành công hoặc callback dừng worker/job báo ngắt. Không kết thúc sau 10 lần; tham số `rate_limit_attempts` được session bỏ qua ở nhánh hiện tại.
- 429 không tiêu hao ngân sách retry lỗi thường. Không đổi proxy, refresh token, giảm size hay chia range chỉ vì 429.
- `Retry-After` và các RateLimit-Reset được ghi nhận chẩn đoán; lịch chờ reference không lấy chúng làm thời gian chờ.
- Việc chờ của managed session kiểm tra ngắt từng lát tối đa 1 giây khi có callback. Tích hợp với cơ chế hủy/stop sẵn có của dự án API.

Không áp chính sách managed GET này vào POST login. Không sao chép vòng retry giới hạn của raw WebClient hoặc nhánh standalone thay cho managed session.

HTTP 401 của API dữ liệu:

- Refresh token sau khi upstream từ chối token, rồi gửi lại request đang lỗi với cùng cursor/params và Authorization mới.
- Dùng token generation + khóa refresh/singleflight hoặc cơ chế tương đương sẵn có để nhiều worker không cùng login tài khoản. Nếu generation mới đã có thì dùng lại.
- Headers caller cũ không được ghi đè Authorization mới sau refresh.
- List production đặt `authentication_attempts=2` ở crawler: tối đa một refresh khi cùng trang tiếp tục bị 401. Detail/package dùng ngân sách session tương ứng; không thêm vòng login vô hạn bên ngoài.
- Có token managed hợp lệ thì dùng lại; gọi `portal.login()` để nạp token managed không đồng nghĩa POST login mới. Không tự refresh vì timeout/500/429.

Khi list vẫn thất bại sau fallback, service có thể tách range thành `[begin..midpoint]` và `[midpoint+1..end]`, giữ nguyên direction/query_type/status. Không tách do 429, repeated cursor hoặc lỗi checkpoint; range một ngày không tách tiếp. Nếu áp dụng, giữ riêng checkpoint các range con và không để mất dữ liệu đã commit.

Nhịp list mặc định `fast_balanced`: sau trang thành công chưa phải trang cuối, nghỉ `max(0.3,0.520,0.350) + random(0.080..0.180)` giây; nghỉ thêm 2.5–4.5 giây mỗi 25 trang. Đây là phép tính thực tế của code, không phải ba lần sleep cộng lại.

Throttle range mặc định: heavy nếu total >=5.000 hoặc estimated_pages >=100; extreme nếu total >=7.500 hoặc estimated_pages >=150, xét extreme trước. estimated_pages lấy ceil(total/size trang đầu). Heavy nghỉ thêm 5–10 giây mỗi 50 trang; extreme 10–15 giây mỗi 30 trang. Cuối range/tháng, lấy mức nặng nhất của các partition để nghỉ một lần: heavy 5–10 giây, extreme 15–20 giây. Áp dụng khi adaptive/page sleep đang bật.

Không biến mọi field cấu hình thành hành vi mới: `detail/package min_start_gap`, `min_idle_gap`, `jitter` tồn tại trong config nhưng không đồng nghĩa handler production thực thi chúng ở từng request. Các service standalone có delay riêng; không copy delay đó rồi tuyên bố là nhịp production. Giữ cơ chế pacing hiện có nếu không xung đột các quy tắc request trên.

## 7. Chi tiết hóa đơn

Sau overview, dùng khóa của chính item thu được và đúng query_type nguồn:

```text
GET /{query_type}/invoices/detail
nbmst    = str(item.nbmst)
khhdon   = str(item.khhdon)
shdon    = str(item.shdon)
khmshdon = str(item.khmshdon)
```

Không dùng company tax code đang đăng nhập thay `nbmst`: `nbmst` là MST người bán của hóa đơn. Giữ các giá trị khóa nguồn khi gọi HTTP; không chuyển mã thành số làm mất số 0 đầu. Không thêm direction vào path `/detail`, cũng không gửi `state`, date range, sort, size hoặc ttxly.

Header Action vẫn phân biệt purchase/sold như mục 3. Response phải parse được JSON object/dict; JSON array hoặc non-JSON là lỗi. Chuyển payload vào storage/normalizer sẵn có; không tự tạo schema upstream khác.

## 8. XML và HTML dùng chung ZIP

```text
GET /{query_type}/invoices/export-xml
nbmst, khhdon, shdon, khmshdon = cùng bốn khóa và cách stringify như detail
```

Cả XML và HTML đều gọi `export-xml`. Response là ZIP nhị phân, không phải JSON, không phải raw XML và không có endpoint `export-html` trong reference này.

Nếu cần cả XML lẫn HTML thì một request package với Action XML, rồi lấy các file cần thiết từ ZIP. Nếu chỉ HTML thì Action `In hóa đơn (...)`. Không tải cùng ZIP hai lần chỉ vì yêu cầu hai định dạng. Reference desktop wrapper bật cả export_xml và export_html cho cùng lượt tải.

Kiểm tra HTTP 200, body không rỗng, ZIP hợp lệ. Dùng cơ chế giải nén/storage hiện có của dự án API.

Nếu response báo đúng `Không tồn tại hồ sơ gốc của hóa đơn.` thì đánh dấu package unavailable cho item, không coi là token hỏng. Session nhận diện HTTP 500 trên `/invoices/export-xml` với message này là lỗi vĩnh viễn và không retry 500 thông thường; crawler chuyển thành `InvoicePackageUnavailableError`. Không tạo vòng download vô hạn cho item đã xác định unavailable.

Không suy luận có API download PDF từ các endpoint trên. PDF/export nghiệp vụ không thuộc phạm vi sửa lõi request này.

## 9. Excel upstream chỉ khi call-site hiện có cần

Production normalized crawl đọc list JSON; không cần thêm bước tải Excel để lấy danh sách. Reference vẫn có hàm export upstream riêng với contract:

```text
sold:
GET /{query_type}/invoices/export-excel
params = {sort: "tdlap:desc", search: "<cùng biểu thức ngày/status>"}

purchase:
GET /{query_type}/invoices/export-excel-sold
params = {sort: "tdlap:desc", search: "<cùng biểu thức ngày/status>", type: "purchase"}
```

Không gửi size/state. Hàm reference truyền headers caller cung cấp, không tự dựng một Action riêng cho Excel. Response binary phải bắt đầu bằng `PK`; timeout/retry như bảng. Chỉ sửa nhánh này nếu dự án API thực sự sử dụng; không đưa nó thành prerequisite của overview/detail.

## 10. Kiểm chứng và kết quả bàn giao

Thêm/sửa tests bằng mock HTTP hoặc fixtures đã làm sạch, bám vào request được gửi thật tại adapter đang dùng. Không cần credential thật để kiểm tra contract. Tối thiểu chứng minh:

1. Đủ bốn combination list `query/sco-query × purchase/sold`; electronic 5/6/8, cash register không ttxly.
2. Body login đúng bốn field, root context đúng; mỗi authenticate chỉ một POST; phân loại sai password và khóa tài khoản đúng.
3. Header Action đúng từng thao tác, encode đúng; request-id khác nhau giữa các wire attempt.
4. Chia tháng đúng ngày cuối tháng/năm nhuận; biểu thức ngày bắt đầu 00:00:00 và kết thúc 23:59:59.
5. Cursor chuyển đúng, retry cùng cursor, fallback 50→30→15, trang mới reset về 50; phát hiện cursor lặp; không dừng chỉ vì trang ngắn hoặc total đã đạt.
6. Preflight 50 được reuse; preflight fallback nhỏ chỉ lấy total; checkpoint và cảnh báo total mismatch không làm mất dữ liệu.
7. Detail/package đúng bốn query key và query_type; nbmst lấy từ item, không từ account.
8. 401 refresh có giới hạn và thay Authorization mới, không nhiều worker login đồng thời; 429 giữ route/token và tiếp tục sau hơn 10 response 429. Mock sleep để test nhanh; có test ngắt được khi chờ.
9. XML/HTML dùng một ZIP request khi cần cả hai; JSON giả ZIP/ZIP lỗi bị từ chối; thông báo không có hồ sơ gốc kết thúc item dưới dạng unavailable.
10. Public API/worker vẫn giữ contracts cũ và thực sự chạy qua phần crawler đã sửa.

Bàn giao bảng sai khác trước/sau gồm file/hàm, request sai và request đã sửa; danh sách file thay đổi; kết quả test; ví dụ request đã sanitize. Nếu chưa test live, ghi rõ đã kiểm bằng mock/fixture, không khẳng định đã crawl thành công trên cổng thật. Hoàn thành implementation và tests, không chỉ đưa kế hoạch.

---

## Nguồn tham chiếu trong repository desktop

Phần này phục vụ đối chiếu khi có repository nguồn, không yêu cầu dự án API phải có cùng đường dẫn.

- `runtime/python/mia_backend.py`: ProductionBackend, vô hiệu hóa crawler legacy, nối OptimizedInvoiceCrawlPipeline.
- `runtime/python/mia_optimized_source_pipeline.py`: wrapper yêu cầu cả XML và HTML từ một package.
- `runtime/python/vendor/mia_crawl_service/app/worker_runtime/handler.py`: call-site thực tế; `_fetch_overview_core`, `_prepare_overview_core`, `_fetch_overview_preflight_response`, `_fetch_detail_core`, `_fetch_package_core`, `_build_portal`, `_refresh_managed_portal`.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/endpoints.py`: URL và mapping export.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/web_client.py`: headers, request-id, HTTP session.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/auth_crawler.py`: CAPTCHA/login/profile và phân loại lỗi login.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/invoice_crawler.py`: search, cursor, fallback size, throttle, download_export.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/invoice_detail_crawler.py`: detail params/Action.
- `runtime/python/vendor/mia_crawl_service/app/crawlers/invoice_package_crawler.py`: ZIP params/Action/validation/unavailable.
- `runtime/python/vendor/mia_crawl_service/app/services/portal_session.py`: managed retry, 401, 429 và Authorization mới.
- `runtime/python/vendor/mia_crawl_service/app/services/overview_downloader.py`: statuses 5/6/8, normalized request, cảnh báo, chia range lỗi.
- `runtime/python/vendor/mia_crawl_service/app/session_manager/portal_authenticator.py` và `service.py`: one-shot login, reuse token, refresh theo generation.
- `runtime/python/vendor/mia_crawl_service/app/config/crawl_config.py`: cấu hình và profile; phải đọc cùng overrides trong handler.
- `runtime/python/vendor/mia_crawl_service/app/utils/date_utils.py`: chia tháng inclusive.
- `runtime/python/tests/test_portal_request_flow.py`: kiểm tra root context, request-id, list/detail Action.

Lưu ý đối chiếu: comment trong `invoice_crawler.py`/`download_export` còn mô tả retry 429 có giới hạn, trong khi `TaxPortalSession._request` hiện dùng vòng chờ tới thành công/ngắt và bỏ qua override `rate_limit_attempts`. Đặc tả trên ưu tiên code thực thi ở nhánh managed production.
