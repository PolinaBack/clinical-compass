CREATE TABLE aviandr_observations (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL,
 fact_key TEXT NOT NULL, confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
 payload BLOB NOT NULL, source_note TEXT NOT NULL, observed_on TEXT NOT NULL,
 client_token TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(actor_id,client_token)
);
CREATE INDEX aviandr_observations_patient ON aviandr_observations(patient_id,id);
CREATE TABLE aviandr_rules (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES drug_imports(id),
 payload TEXT NOT NULL, checksum TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES accounts(id),
 created_at TEXT NOT NULL, UNIQUE(import_id,checksum)
);
CREATE TABLE aviandr_runs (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id), actor_auth_version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','error')),
 stage TEXT NOT NULL, snapshot_hash TEXT NOT NULL, snapshot BLOB NOT NULL,
 result BLOB, provenance TEXT, error_code TEXT, created_at TEXT NOT NULL,
 started_at TEXT, heartbeat_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX aviandr_one_active ON aviandr_runs(patient_id) WHERE status IN ('queued','running');
CREATE TABLE aviandr_scenarios (
 scenario TEXT PRIMARY KEY CHECK(scenario IN ('C','D','E')),
 patient_id INTEGER UNIQUE NOT NULL REFERENCES patients(id),
 seed_version TEXT NOT NULL, anchor_day TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER aviandr_observations_no_update BEFORE UPDATE ON aviandr_observations
 BEGIN SELECT RAISE(ABORT,'immutable research observation'); END;
CREATE TRIGGER aviandr_observations_no_delete BEFORE DELETE ON aviandr_observations
 BEGIN SELECT RAISE(ABORT,'immutable research observation'); END;
CREATE TRIGGER aviandr_rules_no_update BEFORE UPDATE ON aviandr_rules
 BEGIN SELECT RAISE(ABORT,'immutable reviewed medical rule'); END;
CREATE TRIGGER aviandr_rules_no_delete BEFORE DELETE ON aviandr_rules
 BEGIN SELECT RAISE(ABORT,'immutable reviewed medical rule'); END;
