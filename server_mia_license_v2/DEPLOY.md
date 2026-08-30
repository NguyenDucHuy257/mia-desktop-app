# Deploy MIA License V2

Package này là server độc lập. Nó không được bundle vào Electron, Python crawler runtime hoặc `app.asar`.

## Chuẩn bị

1. Backup canonical `/opt/keys_app/MIA/vip.txt` và kiểm tra SHA/count.
2. Tạo virtualenv riêng và cài `requirements.txt`.
3. Sinh `MIA_LICENSE_TOKEN_SECRET` ngẫu nhiên tối thiểu 32 bytes, lưu trong secret manager.
4. Đặt `MIA_LICENSE_DATABASE=/opt/keys_app/MIA/license.db`.
5. Import legacy read-only; importer không sửa `vip.txt`:

```bash
python -m server_mia_license_v2.import_legacy_mia /opt/keys_app/MIA/vip.txt \
  --database /opt/keys_app/MIA/license.db
```

6. Mount router/app sau HTTPS reverse proxy. Không expose Uvicorn trực tiếp ra Internet.

```bash
uvicorn server_mia_license_v2.app:app --host 127.0.0.1 --port 8765
```

## Shared-server isolation

- Mọi table/query trong module bị khóa `tool=MIA`.
- Không sửa `MIA2/`, `MIA3/`, `GBOT/`, `IDQUICK/` hoặc `GSOFT/`.
- Giữ `/verify-key` cũ trong pilot/grace period.
- Chạy smoke trên HTTPS endpoint trước và sau deploy:

```bash
python -m server_mia_license_v2.smoke_shared_server --base-url https://gotax.vn
```

Smoke chỉ ghi status/non-empty, không in key store.

## Admin activation

Public `/activate` chỉ tạo hoặc kiểm tra activation request ổn định. Kích hoạt thật gọi `MiaLicenseService.admin_activate()` từ authenticated admin command/service và phải audit operator ở integration layer. Không public method này trực tiếp.

## Rollback

1. Tắt route `/license/v2/*` hoặc client feature flag.
2. Không xóa `license.db`; giữ để điều tra/audit.
3. Legacy `/verify-key` tiếp tục trong grace period.
4. Restore database từ backup nếu schema deployment lỗi; tuyệt đối không replace `vip.txt` bằng database output.

Production deployment pending cho đến khi có quyền server, TLS smoke, backup/restore rehearsal và authenticated admin integration.
