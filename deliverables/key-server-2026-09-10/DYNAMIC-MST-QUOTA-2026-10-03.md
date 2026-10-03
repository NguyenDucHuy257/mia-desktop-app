# Quota MST động cho gói VIP<N> / TEST<N> — bản server 03/10/2026

Áp dụng cho `/opt/keys_app/auth.py` (server key dùng chung cho MIA và TAXSOFT/GSOFT).

## Hành vi mới

Trường thứ 5 của dòng key trong `vip.txt` quyết định cách giới hạn MST của gói có số lượng:

| Trường 5 | Ý nghĩa | Khi khách thêm MST mới |
|---|---|---|
| danh sách MST (`0101234567,0107654321`) | khóa cứng phạm vi (hành vi cũ) | MST ngoài danh sách bị từ chối `mst_not_authorized` |
| `o` hoặc trống | **quota động** (mới) | server tự ghi MST vào `<TOOL>/mst_bindings.txt` cho tới khi đủ N; MST thứ N+1 bị từ chối `mst_limit_reached` |

- MST đã ghi được dùng lại bình thường, không tốn thêm lượt.
- Response trả `mst_limit`, `mst_used`, `mst_remaining`, `mst_newly_bound`; `entitlements.allowed_tax_codes` là danh sách đã ghi, `max_tax_codes` là N.
- Trước bản này, gói có số lượng mà trường 5 là `o`/trống bị từ chối `license_policy_invalid`; không có key nào đang hoạt động ở trạng thái đó nên không ảnh hưởng khách hiện tại.
- Gói `VIP`/`v` không đổi. Danh sách khai sẵn vượt N vẫn trả `license_policy_invalid`.
- Nếu kỹ thuật điền danh sách MST vào trường 5 sau khi quota động đã ghi, danh sách khai sẵn thắng.
- Reset quota động của một key: xóa các dòng có cột 1 = `sha256(key)` trong `mst_bindings.txt`. Không xóa file.

## Phiên bản desktop tối thiểu cho quota động

Client cũ không hiển thị/kiểm soát được quota còn trống nên server trả `client_update_required` cho key quota động khi:

| Tool | Tối thiểu |
|---|---|
| TAXSOFT (`GSOFT`) | 2.9.1 |
| MIA | 4.2.5 |

Key khai sẵn danh sách MST giữ sàn cũ (TAXSOFT 2.8.0, MIA 4.0.8).

## Cấp key quota động

```bash
# TAXSOFT, 20 MST tự ghi
python3 license_admin.py issue --key 'KEYV2-...-0912345678' --plan VIP20 --expires 31/12/2027 --contact 0912345678
# MIA: sửa tay MIA/vip.txt
KEYV2-...-0981234567|VIP20|31/12/2027|0981234567|o
```

## Deploy

```bash
cd /opt/keys_app
stamp="$(date +%Y%m%d-%H%M%S)"
tar -C /opt -czf "/opt/keys_app-backup-${stamp}.tgz" keys_app
install -m 0644 /secure/upload/auth.py /opt/keys_app/auth.py
/opt/keys_app/venv/bin/python -m py_compile /opt/keys_app/auth.py /opt/keys_app/app.py
systemctl restart keys_app.service
systemctl status keys_app.service --no-pager
curl -sS -o /dev/null -w 'verify-key-v2=%{http_code}\n' http://127.0.0.1:8001/verify-key-v2   # 405 là đúng (GET)
```

Chỉ thay `auth.py`; `app.py`, `mia_recovery.py`, dữ liệu `MIA/`, `GSOFT/` giữ nguyên. Rollback: restore archive backup như `RECOVERY-DEPLOY.md`.

## Kiểm tra sau deploy

1. Key VIP khai sẵn MST (khách hiện tại): mở app, thêm MST trong danh sách → OK; ngoài danh sách → từ chối. Không đổi.
2. Tạo key test `VIP2|...|o` cho một máy TAXSOFT 2.9.1: thêm MST 1, 2 → OK và `mst_bindings.txt` có 2 dòng; MST 3 → "Key đã sử dụng hết số lượng mã số thuế được cấp"; tiêu đề cửa sổ hiện `Gói VIP2: đã dùng 2/2 MST`.
3. Cùng key đó trên TAXSOFT 2.9.0 → "Phiên bản TAXSOFT hiện tại chưa hỗ trợ gói key được cấp".

## Test trong repo

```bash
python -m pytest shared_key_server_mia/tests deliverables/taxsoft-key-policy-2026-09-27 -q
npx vitest run tests/unit/license-entitlements.test.ts
```
