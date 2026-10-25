CREATE TABLE care_orders (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 kind TEXT NOT NULL CHECK(kind IN ('lab','instrumental','consultation','advice')),
 payload BLOB NOT NULL,
 status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','completed','cancelled')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 client_token TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(author_id,client_token),
 UNIQUE(id,patient_id)
);
CREATE INDEX care_orders_patient ON care_orders(patient_id,id);
CREATE TABLE care_order_files (
 id INTEGER PRIMARY KEY,
 order_id INTEGER NOT NULL,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 owner_id INTEGER NOT NULL REFERENCES accounts(id),
 filename TEXT NOT NULL,
 mime TEXT NOT NULL CHECK(mime IN ('application/pdf','image/png','image/jpeg')),
 byte_size INTEGER NOT NULL CHECK(byte_size>0 AND byte_size<=716800),
 sha256 TEXT NOT NULL,
 content BLOB,
 comment TEXT NOT NULL DEFAULT '',
 scan_status TEXT NOT NULL,
 scan_engine TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 deleted_at TEXT,
 FOREIGN KEY(order_id,patient_id) REFERENCES care_orders(id,patient_id)
);
CREATE INDEX care_order_files_order ON care_order_files(order_id,id);
