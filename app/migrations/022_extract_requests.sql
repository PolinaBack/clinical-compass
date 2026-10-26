CREATE TABLE extract_requests (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 requested_by INTEGER NOT NULL REFERENCES accounts(id),
 client_token TEXT NOT NULL,
 payload BLOB NOT NULL,
 status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received','in_progress','clarify','ready','declined')),
 revision INTEGER NOT NULL DEFAULT 1,
 document_id INTEGER UNIQUE REFERENCES patient_extracts(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(requested_by,client_token)
);
CREATE INDEX extract_requests_patient ON extract_requests(patient_id,updated_at,id);
CREATE TABLE extract_request_events (
 id INTEGER PRIMARY KEY,
 request_id INTEGER NOT NULL REFERENCES extract_requests(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id),
 actor_role TEXT NOT NULL CHECK(actor_role IN ('doctor','patient')),
 client_token TEXT NOT NULL,
 status TEXT NOT NULL,
 payload BLOB NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(actor_id,client_token)
);
CREATE INDEX extract_request_events_request ON extract_request_events(request_id,id);
CREATE TABLE extract_request_files (
 id INTEGER PRIMARY KEY,
 request_id INTEGER NOT NULL REFERENCES extract_requests(id),
 event_id INTEGER REFERENCES extract_request_events(id),
 owner_id INTEGER NOT NULL REFERENCES accounts(id),
 filename TEXT NOT NULL,
 mime TEXT NOT NULL,
 byte_size INTEGER NOT NULL,
 sha256 TEXT NOT NULL,
 content BLOB NOT NULL,
 scan_status TEXT NOT NULL,
 scan_engine TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX extract_request_files_request ON extract_request_files(request_id,id);
CREATE TRIGGER extract_request_events_no_update BEFORE UPDATE ON extract_request_events
 BEGIN SELECT RAISE(ABORT,'immutable request history'); END;
CREATE TRIGGER extract_request_events_no_delete BEFORE DELETE ON extract_request_events
 BEGIN SELECT RAISE(ABORT,'immutable request history'); END;
