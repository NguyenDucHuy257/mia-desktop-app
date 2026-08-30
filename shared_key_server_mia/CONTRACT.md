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
  "key": "MIAV2-<32 lowercase hex>",
  "device_id": "<stable local UUID/id>",
  "phone": "0981234567 hoặc chuỗi rỗng khi thử legacy V1",
  "hardware": {
    "system_uuid": "<sha256>",
    "bios_serial": "<sha256>",
    "baseboard_serial": "<sha256>",
    "machine_guid": "<sha256>",
    "cpu_id": "<sha256>",
    "disk_serial": "<sha256>"
  },
  "legacy_keys": ["key<29hex>"]
}
```

`key = "MIAV2-" + sha256("MIA|" + device_id)[:32]`. Phone không tham gia
công thức nên update phone hoặc recovery không rotate canonical key.

Response:

```json
{
  "valid": true,
  "key": "MIAV2-...",
  "device_id": "<canonical device_id>",
  "phone": "0981234567 hoặc chuỗi rỗng",
  "phone_status": "verified|pending",
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
