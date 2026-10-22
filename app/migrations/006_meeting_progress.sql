ALTER TABLE meeting_jobs ADD COLUMN stage TEXT NOT NULL DEFAULT '';
ALTER TABLE meeting_jobs ADD COLUMN processed_seconds REAL NOT NULL DEFAULT 0;
ALTER TABLE meeting_jobs ADD COLUMN units_done INTEGER NOT NULL DEFAULT 0;
ALTER TABLE meeting_jobs ADD COLUMN units_total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE meeting_jobs ADD COLUMN started_at TEXT;
ALTER TABLE meeting_jobs ADD COLUMN heartbeat_at TEXT;
ALTER TABLE meeting_jobs ADD COLUMN progress_at TEXT;
