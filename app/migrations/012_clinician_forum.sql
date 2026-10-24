CREATE TABLE clinician_topics (
 id INTEGER PRIMARY KEY,
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 kind TEXT NOT NULL CHECK(kind IN ('case','question')),
 title TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','hidden','deleted')),
 revision INTEGER NOT NULL DEFAULT 1,
 submission_id TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX clinician_topics_feed ON clinician_topics(status,id);
CREATE TABLE clinician_comments (
 id INTEGER PRIMARY KEY,
 topic_id INTEGER NOT NULL REFERENCES clinician_topics(id),
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','hidden','deleted')),
 revision INTEGER NOT NULL DEFAULT 1,
 submission_id TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX clinician_comments_thread ON clinician_comments(topic_id,status,id);
CREATE TABLE clinician_privacy_reports (
 id INTEGER PRIMARY KEY,
 topic_id INTEGER REFERENCES clinician_topics(id),
 comment_id INTEGER REFERENCES clinician_comments(id),
 reporter_id INTEGER NOT NULL REFERENCES accounts(id),
 created_at TEXT NOT NULL,
 CHECK((topic_id IS NOT NULL AND comment_id IS NULL) OR (topic_id IS NULL AND comment_id IS NOT NULL))
);
