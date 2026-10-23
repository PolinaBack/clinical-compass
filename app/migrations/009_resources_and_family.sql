CREATE TABLE resource_items (
 id INTEGER PRIMARY KEY, seed_key TEXT UNIQUE, title TEXT NOT NULL, url TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', publisher TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES accounts(id), created_at TEXT NOT NULL,
 link_status TEXT NOT NULL DEFAULT 'not_checked', checked_at TEXT
);
CREATE TABLE resource_assignments (
 id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL REFERENCES resource_items(id),
 patient_id INTEGER NOT NULL REFERENCES patients(id), assigned_by INTEGER NOT NULL REFERENCES accounts(id),
 title TEXT NOT NULL, url TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', due_day TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','opened','read','later')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(resource_id,patient_id)
);
CREATE TABLE resource_events (
 id INTEGER PRIMARY KEY, assignment_id INTEGER NOT NULL REFERENCES resource_assignments(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE family_person_meta (
 relative_id INTEGER PRIMARY KEY REFERENCES relatives(id), custom_relation TEXT NOT NULL DEFAULT '',
 sex TEXT NOT NULL DEFAULT 'unknown' CHECK(sex IN ('male','female','unknown')),
 age INTEGER CHECK(age BETWEEN 0 AND 130), deceased INTEGER NOT NULL DEFAULT 0,
 updated_by INTEGER NOT NULL REFERENCES accounts(id), updated_at TEXT NOT NULL
);
CREATE TRIGGER no_additional_parents BEFORE INSERT ON relatives
WHEN NEW.relation IN ('mother','father') AND EXISTS(
 SELECT 1 FROM relatives WHERE patient_id=NEW.patient_id AND (relation=NEW.relation OR slot=NEW.relation))
BEGIN SELECT RAISE(ABORT,'parent_already_exists'); END;
CREATE TRIGGER no_additional_parents_on_update BEFORE UPDATE OF relation,patient_id ON relatives
WHEN NEW.relation IN ('mother','father') AND EXISTS(
 SELECT 1 FROM relatives WHERE patient_id=NEW.patient_id AND id!=NEW.id AND (relation=NEW.relation OR slot=NEW.relation))
BEGIN SELECT RAISE(ABORT,'parent_already_exists'); END;
