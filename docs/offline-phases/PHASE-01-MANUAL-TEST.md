# Phase 1 — hướng dẫn kiểm chứng tay trên Windows

Trạng thái nghiệm thu: `PASS` — người dùng xác nhận ngày 2026-08-18.

## 1. Chuẩn bị và build

Mở PowerShell tại repo, không cần cài dependency crawler:

```powershell
node --version
python --version
npm ci
npm run package:win
```

Kỳ vọng:

- Node từ 24 trở lên.
- `packaged Python runtime smoke: PASS`.
- Có `release\MIA WT Setup 0.1.0.exe`.
- Có `runtime\dist\mia-runtime\mia-runtime.exe` trong workspace build; thư mục này không commit Git.

## 2. Kiểm tra runtime độc lập

```powershell
npm run test:runtime:packaged
```

Kỳ vọng `PASS`, không xuất hiện cửa sổ console Python và không còn `mia-runtime.exe` trong Task Manager sau test.

## 3. Cài trên Windows sạch

1. Copy installer sang Windows VM chưa cài Python.
2. Cài ứng dụng và mở MIA WT.
3. Xác nhận UI mở bình thường.
4. Mở Task Manager → Details, xác nhận đúng một `mia-runtime.exe` khi app chạy.
5. Đóng app; chờ tối đa 3 giây và xác nhận `mia-runtime.exe` biến mất.
6. Mở lại app và xác nhận vẫn chỉ có một runtime process.

## 4. Kiểm tra SQLite và phục hồi

Trong `%APPDATA%`/thư mục user-data của MIA WT, tìm `offline-runtime\mia.sqlite3`:

- file tồn tại sau lần mở đầu;
- đóng/mở app không tạo database thứ hai;
- thư mục `logs` chỉ có log vận hành đã sanitize;
- tìm kiếm trong log không thấy password, token, API key hoặc MST thật.

Không chỉnh database khi app đang chạy. Để kiểm tra file hỏng, dùng bản sao VM/snapshot:

1. Đóng app hoàn toàn.
2. Sao lưu `mia.sqlite3`.
3. Thay nội dung database bằng file rác.
4. Mở app và xác nhận app không tạo database mới đè lên file hỏng.
5. Khôi phục file sao lưu để tiếp tục sử dụng.

## 5. Kiểm tra crash/restart có giới hạn

1. Mở app và xác nhận `mia-runtime.exe` đang chạy.
2. End task riêng `mia-runtime.exe`, không end task Electron.
3. Phase 1 chưa expose business command từ renderer; chạy automated manager test để xác minh bounded recovery:

```powershell
npm test -- --run tests/unit/offline-runtime-manager.test.ts
```

Kỳ vọng 3/3 PASS: chống start trùng, phục hồi sau crash và dừng restart khi vượt giới hạn.

## 6. Báo cáo kết quả

Ghi `PASS`, `FAIL`, `NOT RUN` hoặc `BLOCKED` cho từng mục: build installer, mở app, một runtime process, shutdown sạch, database tồn tại, restart giữ database, log không có secret và test crash/restart.
