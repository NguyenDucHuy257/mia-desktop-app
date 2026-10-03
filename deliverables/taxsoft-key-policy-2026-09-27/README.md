# TAXSOFT key policy add-on

Gói này bổ sung vận hành cấp key TAXSOFT/GSOFT cho server `keys_app` hiện tại.
Không thay thế `auth.py`; bản server đã có KEYV2, migration khách cũ và quota MST.

## Quy ước gói

- `VIP` hoặc legacy `v`: không giới hạn MST. Field 5 là `o`/rỗng, hoặc danh sách MST cố định nếu muốn khóa phạm vi.
- `VIP1`, `VIP2`, ...: tối đa đúng số MST trong hậu tố. Field 5 có hai cách dùng:
  - `o`/rỗng = **quota động**: server tự ghi N MST đầu tiên khách thêm vào `mst_bindings.txt`, MST thứ N+1 bị chặn (`mst_limit_reached`). Cần TAXSOFT ≥ 2.9.1, MIA ≥ 4.2.5.
  - danh sách MST = khóa cứng phạm vi, khách không tự thêm được MST khác.
- `TEST`, `TEST1`: thử nghiệm 1 MST (khai sẵn hoặc quota động 1 MST).
- `TEST2`, ...: thử nghiệm tối đa N MST.
- Muốn reset quota động cho một key: xóa các dòng có `sha256(key)` ở cột 1 trong `mst_bindings.txt` (không xóa file).
- Mọi gói TEST chỉ được tra cứu trong `GSOFT_TEST_DATE_FROM..GSOFT_TEST_DATE_TO`.
- Field `expiry` vẫn là ngày hết hạn sử dụng key; giới hạn ngày dữ liệu TEST là một kiểm tra riêng.

Định dạng canonical:

```text
KEY|PLAN|dd/mm/YYYY|CONTACT|MST1,MST2
```

## Cấu hình server

Chép `taxsoft_policy.py` cạnh `auth.py`, rồi thêm sau dòng import `auth` trong `app.py`:

```python
import taxsoft_policy  # noqa: F401
```

Biến môi trường:

```text
GSOFT_TEST_DATE_FROM=2026-09-01
GSOFT_TEST_DATE_TO=2026-09-30
GSOFT_MIN_LIMITED_VERSION=2.8.0
```

Server sẽ fail khi ngày hoặc version cấu hình sai.

## Cấp khách mới

Khách mở TAXSOFT, nhập số điện thoại và gửi KEYV2 đang hiển thị. Không tự tạo key khác.

```bash
python3 license_admin.py issue \
  --key 'KEYV2-...-0912345678' \
  --plan VIP2 \
  --expires 31/12/2027 \
  --contact 0912345678 \
  --mst 0101234567 \
  --mst 0107654321
```

Lệnh ghi nguyên tử vào `GSOFT/vip.txt`, tạo `vip.txt.bak` trước khi thay đổi và dùng chung `.license_v2.lock` với API.

## Bắt buộc trước khi phát hành TAXSOFT dùng KEYV2

Chụp toàn bộ key GSOFT cũ đang có trong `vip.txt` sang registry migration:

```bash
python3 seed_gsoft_legacy.py --folder /opt/keys_app/GSOFT --dry-run
python3 seed_gsoft_legacy.py --folder /opt/keys_app/GSOFT
```

Script chỉ lấy dòng bắt đầu bằng `KEY` nhưng không phải `KEYV2-`; bỏ qua dòng update và không xóa dữ liệu. Chạy lại không tạo trùng. Trước khi ghi, `legacy_vip.txt.bak` được tạo tự động.

Thứ tự rollout đúng:

1. Chạy `seed_gsoft_legacy.py` trên server.
2. Xác nhận `legacy_vip.txt` có đủ key cũ.
3. Deploy server hỗ trợ `/verify-key-v2`.
4. Mới phát hành TAXSOFT dùng KEYV2.
5. Lần đầu khách cũ chạy bản mới, server migration key cũ sang KEYV2 và lưu ánh xạ vào `legacy_migrations.json`.

## Cấp lại/nâng gói khách cũ

Có thể truyền legacy key hoặc KEYV2. Nếu `legacy_migrations.json` đã có ánh xạ, công cụ cập nhật đồng thời dòng legacy và canonical để quyền không bị quay lại sau migration/reconcile.

```bash
python3 license_admin.py update \
  --key 'KEY...' \
  --plan VIP1 \
  --expires 31/12/2027 \
  --contact 0912345678 \
  --mst 0101234567
```

Khách cũ chưa migration tiếp tục dùng endpoint `/verify-key`. Khi chạy TAXSOFT mới, client gửi các legacy candidate dựng từ số điện thoại và phần cứng; server chỉ migration nếu khớp chính xác một legacy key hợp lệ, sau đó giữ ổn định canonical KEYV2.

## Kiểm tra trước khi ghi

```bash
python3 license_admin.py issue ... --dry-run
```

Không xóa `legacy_vip.txt`, `device_bindings.json`, `legacy_migrations.json` hoặc `mst_bindings.txt` thủ công.
