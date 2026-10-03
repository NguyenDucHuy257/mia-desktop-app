# Rà soát error report 03/10/2026 (MIA 4.2.2 và 4.2.4)

Nguồn: `error report/MIA_Log_Loi_2026-10-03T03-04-22-898Z.json` (4.2.2), `error report/MIA_Log_Loi_2026-10-03T09-00-36-438Z.json` (4.2.4) và ảnh chụp lỗi "Không thể hoàn thành tác vụ tải XML/HTML/PDF" ở 33/79 (63%).

## 1. Tải PDF bị hủy sau ~120 giây — xác nhận, đã sửa

Bằng chứng trong log: 5 lần `artifact_download_failed error=artifact_export_timeout`. Bốn lần còn đủ `artifacts.batch.start` đều thất bại sau 122–124 giây, kể cả khi chỉ chọn 1 tài khoản:

| batch.start | artifact_download_failed | Chênh |
|---|---|---|
| 03:01:57.643Z | 03:03:59.864Z | 122 s |
| 08:25:23.880Z | 08:27:28.042Z | 124 s |
| 08:47:02.902Z | 08:49:07.136Z | 124 s |
| 08:54:30.024Z | 08:56:32.915Z | 123 s |

Nguyên nhân trong code: `_reap_stale_artifact_task` hủy tác vụ khi `last_activity_monotonic` quá 120 s, nhưng nhánh `artifacts.batch.start` chỉ ghi `started_monotonic` và tạo `ArtifactBatchCoordinator` không có callback tiến độ. Watchdog vì thế tính từ lúc bắt đầu, không phải từ lần tiến triển cuối. Luồng `artifacts.export.start` (Excel) đã làm đúng từ trước.

Sửa:
- `runtime/python/mia_runtime.py`: `artifacts.batch.start` khởi tạo `last_activity_monotonic` và truyền `state_callback`/`activity_callback` để mọi thay đổi trạng thái làm mới heartbeat.
- `runtime/python/mia_artifact_pipeline.py`: coordinator nhận `activity_callback`, gọi trong `_emit`, khi gói hóa đơn sẵn sàng (`ready`) và ngay trước khi render từng PDF.
- Timeout 120 s vẫn giữ, nhưng giờ là timeout **không tiến triển**.

## 2. "Máy chủ chưa trả quyền sử dụng" sau khi verify thành công — xác nhận, đã sửa

Chuỗi sự kiện 08:58:43–08:58:46Z (15:58 giờ VN): `results.materialStatus` → `license_init_started` → `license_verify_response valid=true reason=ok 6/6` → `license_init_failed code=EPERM` → `runtime_rpc_end code=license_policy_missing`. Lần poll 5 giây sau thành công. Không có dấu hiệu key hết hạn hay server thiếu quyền; lỗi là ghi file trạng thái cục bộ (EPERM trên Windows thường do antivirus/indexer giữ file vừa ghi hoặc quyền thư mục).

Phát hiện thêm từ log: **mỗi lần poll `results.materialStatus` (5 giây/lần) đều gọi server verify (2,5–3,5 s) và ghi lại 4 file license**. Điều này vừa làm poll chậm vừa nhân số lần đụng file.

Sửa:
- `electron/license/protected-license-store.cjs`: rename cuối của ghi nguyên tử thử lại tối đa 6 lần khi EPERM/EBUSY/EACCES; lỗi cuối cùng mang tên file (`licenseFile`) để chẩn đoán.
- `electron/license/license-manager.cjs`: `persistActive` ghi từng file qua `persistSafely`; ghi hỏng chỉ log `license_persist_failed {step, code, file}` và **giữ trạng thái active** vì server đã cấp quyền. Thêm `verifyIntervalMs` (main.cjs đặt 60 s): khi đang active, `initialize()` dùng lại kết quả server trong 60 s; `retry()`, thêm/kết nối lại tài khoản vẫn verify ngay.
- `electron/license/entitlements.cjs`: nếu reason là mã lỗi fs (`EPERM`, `EBUSY`, …) thì báo `license_local_state_failed` với thông báo tiếng Việt đúng nguyên nhân thay vì `license_policy_missing`.

## 3. `desktop_source_timeout_wait` bị ghi ERROR — xác nhận, đã sửa

Python ghi `logger.warning(...)` từ logger `mia.desktop_source_pipeline` (không nằm trong danh sách logger có file handler) nên rơi vào `lastResort` của Python: in trần ra stderr, Electron ghi mọi stderr là ERROR. Đồng bộ sau đó hoàn tất 100% và xuất Excel thành công (02:57:21Z), nên đây không phải lỗi cuối cùng.

Sửa:
- `runtime/python/mia_logging.py`: root logger có stderr handler mức WARNING với tiền tố `LEVEL logger message` (vẫn redact).
- `electron/python-runtime-client.cjs`: `runtimeStderrLevel()` đọc tiền tố để ghi `warn`/`info`/`error` tương ứng.

## Test

- Python runtime: `cd runtime/python && python -m pytest tests -q` → 302 passed (thêm 3 test: heartbeat batch.start, coordinator activity, prefix stderr).
- Vitest: thêm test persist EPERM giữ active, throttle verify, mã lỗi cục bộ, retry rename, mapping stderr level.

Chưa đóng gói installer và chưa tái hiện trên máy khách; cần phát hành trong bản tiếp theo (4.2.5).
