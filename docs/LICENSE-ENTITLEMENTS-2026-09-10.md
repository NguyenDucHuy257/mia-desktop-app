# Sửa giới hạn TEST và rà key cũ — 10/09/2026

## Nguyên nhân trong source

`shared_key_server_mia/mia_v2.py` trước đây không trả plan/MST/ngày dữ liệu.
Electron chỉ kiểm tra valid/expired/reason nên TEST1 vẫn có thể thao tác MST khác
và chọn cả năm. Đây là thiếu sót ở cả hợp đồng server và thực thi phía desktop.
Chưa xác minh revision đang chạy trên server production.

## Bản sửa

- Server trả `entitlements` theo CONTRACT.md; desktop giữ quyền trong phiên và kho
  mã hóa. Thiếu policy hoặc TEST không có MST cụ thể sẽ từ chối, không coi là VIP.
- TEST/TEST1 = 1 MST; TEST2 = tối đa 2 MST, theo danh sách trong key. Không tính lại
  quota mỗi lượt chọn; xóa/thêm tài khoản hoặc đổi trang không mở thêm quyền.
- Dữ liệu dùng thử chỉ từ 01/08/2026 đến 31/08/2026. Picker tự đưa khoảng ngoài
  quyền về tháng này và không nhận nhập tay ngoài giới hạn. Giới hạn ngày dữ liệu
  khác với ngày hết hạn sử dụng key.
- Electron kiểm tra trước RPC đồng bộ, MVT, xem kết quả, XML/HTML/PDF, Excel và
  GTGT; kiểm tra cả tài khoản đã lưu và xuất từ cache. Query bỏ ngày được giới hạn
  lại; query cố tình vượt ngày bị từ chối với thông báo cụ thể.
- Key trả phí vẫn giữ danh sách MST/`o` và không bị giới hạn tháng dùng thử.
- Sửa parser ngày hết hạn của dạng `key|expiry|l|contact|scope`; ngày hỏng không
  được coi là vô hạn. Key phone còn hạn không bị che bởi candidate V1 hết hạn.
- Bổ sung công thức serial-only + phone có trong source 3.9.0, cùng vị trí
  `__pycache__/sdt.txt` ở userData/thư mục chạy/thư mục executable. Không scan máy,
  không dùng fallback serial `N/A` để claim bản quyền.

## Kết quả file người dùng cung cấp

Audit read-only, chỉ lưu số tổng hợp, không lưu key/điện thoại/MST:

- 2.125 dòng key: 2.100 có hình dạng legacy được hỗ trợ, 1 KEYV2,
  24 nằm ngoài hai hình dạng legacy đang hỗ trợ.
- 312 dòng giới hạn MST; 1.813 dòng không giới hạn MST.
- 9 dòng dùng ngày hết hạn ở cột thứ hai.
- Không có dòng plan TEST trong file này; TEST được kiểm thử bằng dữ liệu giả.
- Trong 24 dòng ngoài mẫu có 7 dạng `key<64 hex>`, các dòng còn lại có
  chiều dài/định dạng khác. Không có đủ bằng chứng công thức để tự nhận các key này.

Số dòng có hình dạng hợp lệ **không phải tỷ lệ migrate thành công**. Tự chuyển
còn cần key hợp lệ trong namespace MIA trên server, còn hạn, đúng công thức phần
cứng, điện thoại và binding. Source cũ được đối chiếu gọi namespace MIA2;
server mới đọc các key MIA legacy có hình dạng đã xác minh từ MIA2 làm nguồn chuyển,
nhưng chỉ ghi KEYV2/binding/mapping vào MIA và không thay đổi MIA2.

Với key chuẩn đã có trên MIA, người dùng không phải xin cấp lại KEYV2: server tự
append key mới và giữ metadata, lưu mapping để lần sau dùng lại. Không tìm thấy
số điện thoại cũ thì vẫn cần nhập điện thoại một lần; không đồng nghĩa cấp key tay.
Các định dạng chưa xác minh, sai phần cứng hoặc hết hạn không được tự cấp quyền.

## Rollout

1. Backup dữ liệu server theo runbook hiện có, cập nhật module server trước.
2. Kiểm tra response TEST1/TEST2/V trả đủ entitlements, binding/migration giữ nguyên.
3. Sau đó build/phát hành desktop; giữ nguyên thư mục userData và cơ sở dữ liệu.
4. Smoke test trên máy có key thật được phép: MST hợp lệ/ngoài quyền, tháng 8/
   ngoài tháng 8, dữ liệu cache, restart, chuyển key cũ.

Chưa deploy server, chưa đóng gói installer hoặc kiểm thử bằng key khách hàng thật
trong lượt sửa này. Không cần sửa hàng loạt key chuẩn chỉ để thêm JSON entitlements.
