# Triển khai email khôi phục vào `keys_app.service`

Service đã xác định: `/opt/keys_app/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8001 --workers 1`.

## 1. Thu hồi SMTP app password cũ

Thu hồi mật khẩu đã từng gửi qua hội thoại và tạo App Password mới. Không ghi secret vào source, Git, `/opt/keys_app` hoặc ảnh chụp màn hình.

## 2. Backup và chép file

```bash
stamp="$(date +%Y%m%d-%H%M%S)"
tar -C /opt -czf "/opt/keys_app-backup-${stamp}.tgz" keys_app

# Tiến trình bạn cung cấp đang chạy bằng root. Nếu cấu hình service đã đổi,
# kiểm tra `User`/`Group` trước và thay owner tương ứng.
install -o root -g root -m 0644 /secure/upload/app.py /opt/keys_app/app.py
install -o root -g root -m 0644 /secure/upload/mia_recovery.py /opt/keys_app/mia_recovery.py
install -d -o root -g root -m 0700 /opt/keys_app/MIA/recovery

/opt/keys_app/venv/bin/python -m py_compile \
  /opt/keys_app/app.py /opt/keys_app/auth.py \
  /opt/keys_app/mia_v2.py /opt/keys_app/mia_recovery.py
```

Xác nhận lại cấu hình thực tế. Giá trị `User=` trống nghĩa là systemd chạy bằng root:

```bash
systemctl show keys_app.service -p User -p Group
```

## 3. Cấu hình secret ngoài source

```bash
install -d -o root -g root -m 0700 /etc/keys_app
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
nano /etc/keys_app/mia-mail.env
chmod 0600 /etc/keys_app/mia-mail.env
```

Nội dung file, thay hai placeholder bằng giá trị mới:

```dotenv
MIA_SMTP_HOST=smtp.gmail.com
MIA_SMTP_PORT=465
MIA_SMTP_USER=hvsoft.contact@gmail.com
MIA_SMTP_APP_PASSWORD=<APP_PASSWORD_MOI_KHONG_COMMIT>
MIA_RECOVERY_SECRET=<KET_QUA_LENH_SECRETS>
MIA_RECOVERY_DB=/opt/keys_app/MIA/recovery/recovery.sqlite3
```

Gắn EnvironmentFile vào đúng service:

```bash
systemctl edit keys_app.service
```

```ini
[Service]
EnvironmentFile=/etc/keys_app/mia-mail.env
```

## 4. Restart và kiểm tra

```bash
systemctl daemon-reload
systemctl restart keys_app.service
systemctl status keys_app.service --no-pager
journalctl -u keys_app.service -n 100 --no-pager
curl -sS -o /dev/null -w 'verify-key-v2=%{http_code}\n' \
  http://127.0.0.1:8001/verify-key-v2
```

Kết quả `405` là đúng vì lệnh trên dùng GET. Email/OTP dùng chung endpoint `POST /verify-key-v2` với trường `action`; client cũ không gửi trường này nên vẫn chạy nhánh xác minh key cũ nguyên trạng. Không cần tạo proxy `/mia/` riêng.

Kiểm tra endpoint hiện có từ ngoài server:

```bash
curl -sS -o /dev/null -w 'public-verify-key-v2=%{http_code}\n' \
  https://gotax.vn/verify-key-v2
```

Kết quả cũng phải là `405`. Không cần thay đổi Nginx nếu endpoint key V2 công khai hiện tại đã hoạt động.

Không test SMTP bằng cách in biến môi trường. Dùng một license thử hợp lệ qua ứng dụng, đăng ký email thử, kiểm tra thư đến và OTP.

## 5. Rollback

```bash
systemctl stop keys_app.service
mv /opt/keys_app "/opt/keys_app.failed-$(date +%Y%m%d-%H%M%S)"
tar -C /opt -xzf /opt/keys_app-backup-<STAMP>.tgz
systemctl start keys_app.service
```
