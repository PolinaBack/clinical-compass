CREATE TABLE intake_drafts (
 patient_id INTEGER PRIMARY KEY REFERENCES patients(id), revision INTEGER NOT NULL DEFAULT 0,
 catalog_version TEXT NOT NULL, answers_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
CREATE TABLE intake_entities (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), kind TEXT NOT NULL,
 version INTEGER NOT NULL, data_json TEXT NOT NULL, updated_by INTEGER NOT NULL REFERENCES accounts(id),
 updated_at TEXT NOT NULL, client_token TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX intake_entity_token ON intake_entities(client_token) WHERE client_token<>'';
CREATE INDEX intake_entities_patient ON intake_entities(patient_id,kind,id);
CREATE TABLE intake_changes (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), actor_id INTEGER NOT NULL REFERENCES accounts(id),
 kind TEXT NOT NULL, object_id INTEGER, revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE intake_submissions (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), revision INTEGER NOT NULL,
 catalog_version TEXT NOT NULL, payload TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL,
 UNIQUE(patient_id,revision)
);
CREATE TABLE intake_reviews (
 id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL REFERENCES intake_submissions(id),
 target TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('confirmed','clarify','rejected','unreadable')),
 note TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL
);
CREATE TABLE clinical_attachments (
 id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), owner_id INTEGER NOT NULL REFERENCES accounts(id),
 scope TEXT NOT NULL CHECK(scope IN ('intake','diary')), category TEXT NOT NULL, entity_id INTEGER REFERENCES intake_entities(id),
 filename TEXT NOT NULL, mime TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB,
 study_date TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '', sensitive INTEGER NOT NULL DEFAULT 0,
 scan_status TEXT NOT NULL, scan_engine TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX clinical_attachment_scope ON clinical_attachments(patient_id,scope,category,id);
CREATE TABLE diary_question_versions (
 metric_id INTEGER NOT NULL REFERENCES diary_metrics(id), version INTEGER NOT NULL,
 definition TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL,
 PRIMARY KEY(metric_id,version)
);
CREATE TABLE diary_question_config (
 metric_id INTEGER PRIMARY KEY REFERENCES diary_metrics(id), version INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 FOREIGN KEY(metric_id,version) REFERENCES diary_question_versions(metric_id,version)
);
CREATE TABLE diary_answer_details (
 diary_id INTEGER NOT NULL REFERENCES diary(id), metric_id INTEGER NOT NULL REFERENCES diary_metrics(id),
 version INTEGER NOT NULL, status TEXT NOT NULL, answer_json TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', discuss INTEGER NOT NULL DEFAULT 0,
 actor_id INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL,
 PRIMARY KEY(diary_id,metric_id), FOREIGN KEY(metric_id,version) REFERENCES diary_question_versions(metric_id,version)
);
CREATE TABLE intake_preferences (
 patient_id INTEGER PRIMARY KEY REFERENCES patients(id), hide_weight INTEGER NOT NULL DEFAULT 0,
 hide_nutrition INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 updated_by INTEGER NOT NULL REFERENCES accounts(id), updated_at TEXT NOT NULL
);
CREATE TABLE intake_entity_links (
 patient_id INTEGER NOT NULL REFERENCES patients(id), left_id INTEGER NOT NULL REFERENCES intake_entities(id),
 right_id INTEGER NOT NULL REFERENCES intake_entities(id), created_by INTEGER NOT NULL REFERENCES accounts(id), created_at TEXT NOT NULL,
 PRIMARY KEY(left_id,right_id), CHECK(left_id<right_id)
);
