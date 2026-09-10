# MIA contract trên shared key server

Endpoint hiện hữu:

```http
POST /verify-key-v2
Content-Type: application/json
```

Request:

```json
{
  "tool": "MIA",
  "key": "KEYV2-<32 lowercase hex>-<phone>",
  "device_id": "<stable local UUID/id>",
  "phone": "0981234567",
  "hardware": {
    "system_uuid": "<sha256>",
    "bios_serial": "<sha256>",
    "baseboard_serial": "<sha256>",
    "machine_guid": "<sha256>",
    "cpu_id": "<sha256>",
    "disk_serial": "<sha256>"
  },
  "legacy_keys": ["key<29hex>", "KEY<29hex><phone>"]
}
```

`key = "KEYV2-" + sha256("MIA|" + device_id)[:32] + "-" + phone`.
Client không gọi V2 khi chưa có phone. Phone mới không tự được coi là license
đã active; shared server vẫn quyết định activation/recovery.

Response:

```json
{
  "valid": true,
  "key": "KEYV2-...-0981234567",
  "device_id": "<canonical device_id>",
  "phone": "0981234567",
  "phone_status": "verified",
  "hardware_profile": {"system_uuid": "<sha256>"},
  "expires_at": "31/12/2027",
  "expired": false,
  "migrated": true,
  "recovered": false,
  "hardware_match": 1.0,
  "hardware_matches": 6,
  "reason": "ok",
  "entitlements": {
    "version": 1,
    "plan": "TEST1",
    "trial": true,
    "max_tax_codes": 1,
    "allowed_tax_codes": ["0123456789"],
    "date_from": "2026-08-01",
    "date_to": "2026-08-31"
  }
}
```

Kết quả hợp lệ luôn dùng `reason: "ok"`; trạng thái migrate/recovery nằm trong
`migrated`/`recovered`. Các `reason` từ chối cần xử lý gồm `phone_required`,
`key_not_activated`, `legacy_key_expired`,
`hardware_mismatch_below_50_percent`, `recovery_ambiguous` và
`legacy_migration_record_incomplete`.

Server giữ original legacy row, append canonical row với nguyên metadata/expiry,
và lưu binding/migration JSON chỉ dưới `/opt/keys_app/MIA`.

## Quyền sử dụng (bắt buộc từ bản sửa 10/09/2026)

- `TEST` / `TEST1`: tối đa 1 MST khai báo ở trường thứ 5 của dòng key.
- `TEST2`, `TEST<n>`: tối đa n MST trong danh sách trường thứ 5, phân cách dấu phẩy.
- Các key TEST chỉ cho phép dữ liệu **01/08/2026–31/08/2026**, độc lập ngày hết hạn key.
- `v` / `VIP`: không giới hạn ngày dữ liệu; MST là danh sách trường thứ 5.
  `o` hoặc thiếu trường này ở key trả phí giữ nghĩa không giới hạn MST của kho cũ.
- MST chi nhánh là định danh riêng, không tự mở quyền tất cả chi nhánh của MST mẹ.
- TEST thiếu danh sách MST, ghi `o`, vượt số MST, hoặc loại key lạ trả `license_policy_invalid`.
- Hỗ trợ cả `key|v|expiry|contact|scope` và `key|expiry|l|contact|scope`.
- Desktop mới từ chối response thiếu `entitlements` với `license_policy_missing`;
  không suy diễn thiếu quyền thành VIP. **Cập nhật server trước khi phát hành desktop mới.**
- Metadata được giữ nguyên khi migrate; key đã migrate dùng bản ghi KEYV2 làm nguồn quyền.
  Không sửa/cấp lại toàn bộ key chỉ để thêm trường JSON này.
- Source 3.9.0 gọi `tool=MIA2`. Server chỉ đọc các key MIA legacy có hình dạng
  đã xác minh từ `MIA2/vip.txt`, seed vào `MIA/legacy_vip.txt`, rồi tạo state mới
  dưới `MIA`. Không sửa/xóa `MIA2` và không claim key có hình dạng lạ.

Kiểm tra read-only một registry (chỉ in số lượng, không in key/MST/điện thoại):

```powershell
python -m shared_key_server_mia.audit_registry 'path/to/vip.txt'
```
