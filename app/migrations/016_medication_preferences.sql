CREATE TABLE medication_preference_versions (
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 revision INTEGER NOT NULL CHECK(revision>0),
 actor_id INTEGER NOT NULL REFERENCES accounts(id),
 status TEXT NOT NULL CHECK(status IN ('draft','sent')),
 payload BLOB NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(patient_id,revision)
);
CREATE INDEX medication_preferences_sent ON medication_preference_versions(patient_id,status,revision);
CREATE TRIGGER medication_preferences_no_update BEFORE UPDATE ON medication_preference_versions
 BEGIN SELECT RAISE(ABORT,'immutable medication preference history'); END;
CREATE TRIGGER medication_preferences_no_delete BEFORE DELETE ON medication_preference_versions
 BEGIN SELECT RAISE(ABORT,'immutable medication preference history'); END;
