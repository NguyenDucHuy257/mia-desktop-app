PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS licenses (
    license_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL CHECK (tool = 'MIA'),
    display_key TEXT NOT NULL UNIQUE,
    phone TEXT,
    phone_status TEXT NOT NULL CHECK (phone_status IN ('verified', 'legacy', 'pending')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'expired', 'revoked')),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    license_id TEXT NOT NULL REFERENCES licenses(license_id),
    tool TEXT NOT NULL CHECK (tool = 'MIA'),
    public_key_pem TEXT NOT NULL,
    device_fingerprint TEXT NOT NULL UNIQUE,
    hardware_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked')),
    bound_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_licenses (
    legacy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL CHECK (tool = 'MIA'),
    legacy_key_hash TEXT NOT NULL,
    raw_line TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    machine_hash29 TEXT,
    phone_guess TEXT,
    expires_at TEXT,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('ready', 'expired', 'manual')),
    imported_at TEXT NOT NULL,
    UNIQUE(tool, legacy_key_hash, raw_line)
);

CREATE INDEX IF NOT EXISTS idx_legacy_mia_hash29
ON legacy_licenses(tool, machine_hash29, parse_status);

CREATE TABLE IF NOT EXISTS legacy_migrations (
    legacy_id INTEGER PRIMARY KEY REFERENCES legacy_licenses(legacy_id),
    license_id TEXT NOT NULL REFERENCES licenses(license_id),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    source TEXT NOT NULL,
    migrated_at TEXT NOT NULL,
    grace_until TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
    challenge_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL CHECK (tool = 'MIA'),
    action TEXT NOT NULL,
    challenge_hash TEXT NOT NULL,
    public_key_pem TEXT NOT NULL,
    device_fingerprint TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS license_tokens (
    token_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL CHECK (tool = 'MIA'),
    license_id TEXT NOT NULL REFERENCES licenses(license_id),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    offline_valid_until TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    event TEXT NOT NULL,
    license_id TEXT,
    device_id TEXT,
    result TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
