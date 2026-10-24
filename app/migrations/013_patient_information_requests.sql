CREATE TABLE patient_information_requests (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 requested_by INTEGER NOT NULL REFERENCES accounts(id),
 topic TEXT NOT NULL CHECK(topic IN ('medicines','allergies','conditions','goals')),
 baseline_hash TEXT NOT NULL,
 assessment_ids TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL,
 answered_at TEXT,
 submission_id INTEGER REFERENCES intake_submissions(id)
);
CREATE UNIQUE INDEX one_pending_information_request
 ON patient_information_requests(patient_id,topic) WHERE answered_at IS NULL;
