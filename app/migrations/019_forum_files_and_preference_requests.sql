CREATE TABLE medication_preference_requests (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 requested_by INTEGER NOT NULL REFERENCES accounts(id),
 client_token TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL,
 answered_at TEXT,
 answer_revision INTEGER
);
CREATE UNIQUE INDEX medication_preference_one_pending
 ON medication_preference_requests(patient_id) WHERE answered_at IS NULL;

CREATE TABLE forum_attachments (
 id INTEGER PRIMARY KEY,
 owner_id INTEGER REFERENCES accounts(id),
 kind TEXT NOT NULL CHECK(kind IN ('topic','comment','faq_question','faq_answer')),
 topic_id INTEGER REFERENCES clinician_topics(id),
 comment_id INTEGER REFERENCES clinician_comments(id),
 faq_id INTEGER REFERENCES forum_questions(id),
 filename TEXT NOT NULL, mime TEXT NOT NULL, byte_size INTEGER NOT NULL,
 sha256 TEXT NOT NULL, content BLOB NOT NULL,
 scan_status TEXT NOT NULL, scan_engine TEXT NOT NULL,
 created_at TEXT NOT NULL,
 CHECK((topic_id IS NULL AND comment_id IS NULL AND faq_id IS NULL)
   OR (kind='topic' AND topic_id IS NOT NULL AND comment_id IS NULL AND faq_id IS NULL)
   OR (kind='comment' AND comment_id IS NOT NULL AND topic_id IS NULL AND faq_id IS NULL)
   OR (kind IN ('faq_question','faq_answer') AND faq_id IS NOT NULL AND topic_id IS NULL AND comment_id IS NULL))
);
CREATE INDEX forum_files_topic ON forum_attachments(topic_id);
CREATE INDEX forum_files_comment ON forum_attachments(comment_id);
CREATE INDEX forum_files_faq ON forum_attachments(faq_id,kind);
