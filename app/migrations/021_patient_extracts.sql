CREATE TABLE patient_extracts (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 revision INTEGER NOT NULL DEFAULT 1,
 published_revision INTEGER,
 client_token TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(author_id,client_token)
);
CREATE INDEX patient_extracts_patient ON patient_extracts(patient_id,id);
CREATE TABLE patient_extract_versions (
 extract_id INTEGER NOT NULL REFERENCES patient_extracts(id),
 revision INTEGER NOT NULL,
 payload BLOB NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES accounts(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(extract_id,revision)
);
CREATE TRIGGER patient_extract_versions_no_update BEFORE UPDATE ON patient_extract_versions
 BEGIN SELECT RAISE(ABORT,'immutable extract history'); END;
CREATE TRIGGER patient_extract_versions_no_delete BEFORE DELETE ON patient_extract_versions
 BEGIN SELECT RAISE(ABORT,'immutable extract history'); END;
