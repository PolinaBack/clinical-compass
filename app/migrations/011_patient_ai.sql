CREATE TABLE patient_ai_runs (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id), actor_auth_version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','error')),
 stage TEXT NOT NULL, snapshot_hash TEXT NOT NULL, snapshot BLOB NOT NULL,
 result BLOB, provenance TEXT, error_code TEXT,
 created_at TEXT NOT NULL, started_at TEXT, heartbeat_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX patient_ai_one_active ON patient_ai_runs(patient_id) WHERE status IN ('queued','running');
CREATE INDEX patient_ai_history ON patient_ai_runs(patient_id,id);
