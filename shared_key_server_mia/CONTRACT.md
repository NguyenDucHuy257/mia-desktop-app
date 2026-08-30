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
  "reason": "legacy_migrated"
}
```

Các `reason` presentation cần xử lý: `ok`, `legacy_migrated`,
`legacy_already_migrated`, `recovered_existing_device`, `phone_required`,
`key_not_activated`, `legacy_key_expired`,
`hardware_mismatch_below_50_percent`, `recovery_ambiguous` và
`legacy_migration_record_incomplete`.

Server giữ original legacy row, append canonical row với nguyên metadata/expiry,
và lưu binding/migration JSON chỉ dưới `/opt/keys_app/MIA`.
