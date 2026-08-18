ALTER TABLE jobs ADD COLUMN stage TEXT;
ALTER TABLE jobs ADD COLUMN current_month_json TEXT;
ALTER TABLE jobs ADD COLUMN error_json TEXT;
ALTER TABLE jobs ADD COLUMN event_sequence INTEGER NOT NULL DEFAULT 0;

CREATE INDEX jobs_updated_at_idx ON jobs(updated_at DESC, job_id DESC);
CREATE INDEX job_events_created_at_idx ON job_events(job_id, created_at);
