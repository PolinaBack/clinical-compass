CREATE TABLE medicine_drafts (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 revision INTEGER NOT NULL CHECK(revision > 0),
 payload BLOB NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(patient_id,author_id)
);
CREATE TABLE medicine_draft_versions (
 draft_id INTEGER NOT NULL REFERENCES medicine_drafts(id),
 revision INTEGER NOT NULL,
 payload BLOB NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(draft_id,revision)
);
CREATE TRIGGER medicine_draft_versions_no_update BEFORE UPDATE ON medicine_draft_versions
 BEGIN SELECT RAISE(ABORT,'immutable medicine draft history'); END;
CREATE TRIGGER medicine_draft_versions_no_delete BEFORE DELETE ON medicine_draft_versions
 BEGIN SELECT RAISE(ABORT,'immutable medicine draft history'); END;
