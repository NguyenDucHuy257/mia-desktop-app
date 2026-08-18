CREATE TABLE accounts (
  account_id TEXT PRIMARY KEY,
  normalized_tax_code TEXT NOT NULL UNIQUE,
  encrypted_password BLOB NOT NULL,
  status TEXT NOT NULL DEFAULT 'unchecked',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL UNIQUE,
  intent_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','waiting_account','running','cancelling','completed','completed_with_warning','failed','cancelled','abandoned')),
  overall_percent INTEGER NOT NULL DEFAULT 0 CHECK (overall_percent BETWEEN 0 AND 100),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE job_events (
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, sequence)
);

CREATE TABLE invoice_overviews (
  overview_id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK (direction IN ('purchase','sold')),
  business_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (account_id, direction, business_key)
);

CREATE TABLE invoice_details (
  detail_id INTEGER PRIMARY KEY,
  overview_id INTEGER NOT NULL REFERENCES invoice_overviews(overview_id) ON DELETE CASCADE,
  line_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE (overview_id, line_key)
);

CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('xml','html','pdf','excel')),
  relative_path TEXT NOT NULL,
  size_bytes INTEGER,
  checksum_sha256 TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
