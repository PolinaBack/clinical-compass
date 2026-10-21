CREATE TABLE drug_imports (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('library','adverse','official')),
 filename TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
 payload TEXT NOT NULL, report TEXT NOT NULL, actor_id INTEGER REFERENCES accounts(id),
 created_at TEXT NOT NULL, committed_at TEXT
);
CREATE TABLE drug_entities (
 kind TEXT NOT NULL, entity_key TEXT NOT NULL, import_id INTEGER NOT NULL REFERENCES drug_imports(id),
 payload TEXT NOT NULL, PRIMARY KEY(kind,entity_key,import_id)
);
CREATE INDEX drug_entities_lookup ON drug_entities(kind,entity_key,import_id DESC);
CREATE TABLE drug_source_events (
 id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES drug_imports(id),
 action TEXT NOT NULL CHECK(action IN ('verify','withdraw')), actor_id INTEGER NOT NULL REFERENCES accounts(id),
 note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER drug_entities_no_update BEFORE UPDATE ON drug_entities BEGIN SELECT RAISE(ABORT,'immutable catalogue history'); END;
CREATE TRIGGER drug_entities_no_delete BEFORE DELETE ON drug_entities BEGIN SELECT RAISE(ABORT,'immutable catalogue history'); END;
CREATE TRIGGER drug_sources_no_update BEFORE UPDATE ON drug_source_events BEGIN SELECT RAISE(ABORT,'immutable source history'); END;
CREATE TRIGGER drug_sources_no_delete BEFORE DELETE ON drug_source_events BEGIN SELECT RAISE(ABORT,'immutable source history'); END;
CREATE TRIGGER drug_imports_no_delete BEFORE DELETE ON drug_imports BEGIN SELECT RAISE(ABORT,'immutable import history'); END;
CREATE TRIGGER drug_imports_keep_snapshot BEFORE UPDATE ON drug_imports
WHEN OLD.committed_at IS NOT NULL OR NEW.id!=OLD.id OR NEW.kind!=OLD.kind OR NEW.payload!=OLD.payload OR NEW.report!=OLD.report OR NEW.content!=OLD.content OR NEW.sha256!=OLD.sha256 OR NEW.filename!=OLD.filename OR NEW.created_at!=OLD.created_at OR NEW.actor_id IS NOT OLD.actor_id
BEGIN SELECT RAISE(ABORT,'immutable import snapshot'); END;
CREATE TABLE drug_bootstrap (version TEXT PRIMARY KEY);
