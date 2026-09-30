# Xử lý key MIA báo "hết hạn" (vận hành server key)

Áp dụng cho server `keys_app` tại `/opt/keys_app/MIA/` sau khi deploy `auth.py` bản 30/09/2026.

## Server tra key ở đâu

- `MIA/vip.txt` là nguồn sự thật. Kỹ thuật chỉ sửa file này.
- `MIA/legacy_vip.txt` là bản cache của key cũ (KEY+29 ký tự). Server tự đồng bộ lại từ `vip.txt` ở mỗi lần xác minh: dòng còn trong `vip.txt` được cập nhật theo `vip.txt`, dòng đã bị xóa khỏi mọi `vip.txt` sẽ bị bỏ.
- `MIA2/vip.txt`, `MIA3/vip.txt`, ... là kho lịch sử, cũng được tính là nguồn. Nếu key còn nằm trong đó thì vẫn còn hiệu lực.
- `MIA/legacy_migrations.json` ghi nhận key cũ đã đổi sang KEYV2. Nếu KEYV2 đó bị xóa khỏi mọi `vip.txt` thì bản ghi migration cũng bị bỏ.

Trước bản này, `legacy_vip.txt` chỉ được thêm vào, không bao giờ cập nhật hay xóa, nên sửa hoặc xóa key trong `vip.txt` không có tác dụng với key cũ, và key KEYV2 bị xóa còn được tự thêm lại từ `legacy_migrations.json`.

## Hướng 1: gia hạn hoặc đổi gói bằng tay

Sửa trực tiếp dòng key của khách trong `MIA/vip.txt` (ngày hết hạn ở trường 3, gói ở trường 2, MST ở trường 5) rồi lưu. Khách mở lại app hoặc bấm "Kiểm tra lại" là dùng được, không cần nhập gì thêm.

## Hướng 2: xóa key

Xóa dòng key của khách khỏi `MIA/vip.txt` và khỏi mọi `MIA<n>/vip.txt` nếu có. Lần mở app kế tiếp, khách thấy màn "Mã kích hoạt chưa được cấp quyền" hiện KEYV2 của máy như lúc mới cài. Kỹ thuật cấp key theo KEYV2 đó bằng `license_admin.py issue`.

## Không cần làm

- Không cần sửa `legacy_vip.txt`, `legacy_migrations.json` hay `device_bindings.json` bằng tay.
- Không cần khách cài lại app hay xóa dữ liệu.
