# Deploy MIA V2 vào shared `/opt/keys_app`

Đây là extension cho `app.py/auth.py` đang phục vụ Taxsoft. Không chạy thêm
Uvicorn, service, listener hoặc database nào.

`app.py` hiện tại không cần đổi functional code vì `KeyV2Req` đã có đủ sáu
field và endpoint đã forward vào `auth.verify_key_v2()`. Patch chỉ thêm MIA
dispatch vào `auth.py`, trước guard GSOFT hiện hữu.

## Files

- `mia_v2.py` -> `/opt/keys_app/mia_v2.py`
- `shared_server.patch` -> áp dụng vào `/opt/keys_app/auth.py`
- `smoke_shared_server.py` -> chạy trước/sau deploy với fixture đã cấp quyền

## Preflight và backup

```bash
cd /opt/keys_app
stamp="$(date +%Y%m%d-%H%M%S)"
sudo tar -C /opt -czf "/opt/keys_app-backup-${stamp}.tgz" keys_app
sudo tar -C /opt/keys_app -czf "/opt/MIA-backup-${stamp}.tgz" MIA
python -m py_compile app.py auth.py
python -c "import app, auth; print('shared-server-import-ok')"
```

Lưu lại hai đường dẫn backup được sinh ra. Không tiếp tục nếu backup hoặc
compile/import thất bại.

## Apply

```bash
cd /opt/keys_app
sudo install -m 0644 /path/to/shared_key_server_mia/mia_v2.py ./mia_v2.py
sudo patch --dry-run -p1 < /path/to/shared_key_server_mia/shared_server.patch
sudo patch -p1 < /path/to/shared_key_server_mia/shared_server.patch
python -m py_compile app.py auth.py mia_v2.py
python -c "import app, auth, mia_v2; print('shared-MIA-import-ok')"
```

Restart đúng service hiện hữu của `/opt/keys_app`; không tạo service mới.

Sau khi bốn smoke checks PASS, cấu hình desktop rollout:

```text
MIA_LICENSE_V2_ENABLED=true
MIA_LICENSE_API_URL=https://gotax.vn
```

Client tự append `/verify-key-v2`; không cấu hình URL `/license/v2` cũ.

## Smoke bắt buộc

Chuẩn bị bốn fixture JSON đã được cấp quyền và không commit chúng:

```bash
python /path/to/shared_key_server_mia/smoke_shared_server.py \
  --base-url https://gotax.vn \
  --legacy-gsoft /secure/smoke/legacy-gsoft.json \
  --legacy-mia /secure/smoke/legacy-mia.json \
  --v2-gsoft /secure/smoke/v2-gsoft.json \
  --v2-mia /secure/smoke/v2-mia.json
```

Smoke chỉ in status/reason/response size; không in key, phone hoặc hardware.

## Rollback

Thay `<BACKUP>` bằng archive đã in ở bước backup:

```bash
sudo systemctl stop <existing-keys-app-service>
sudo mv /opt/keys_app "/opt/keys_app.failed-$(date +%Y%m%d-%H%M%S)"
sudo tar -C /opt -xzf <BACKUP>
python -m py_compile /opt/keys_app/app.py /opt/keys_app/auth.py
sudo systemctl start <existing-keys-app-service>
```

Rollback restore toàn snapshot để `app.py`, `auth.py` và dữ liệu nhất quán.
