CREATE UNIQUE INDEX medicine_draft_patient ON medicine_drafts(id,patient_id);
CREATE TABLE medicine_plan_shares (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 draft_id INTEGER NOT NULL,
 draft_revision INTEGER NOT NULL,
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 payload BLOB NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(draft_id,draft_revision),
 FOREIGN KEY(draft_id,draft_revision) REFERENCES medicine_draft_versions(draft_id,revision),
 FOREIGN KEY(draft_id,patient_id) REFERENCES medicine_drafts(id,patient_id)
);
CREATE INDEX medicine_shares_patient ON medicine_plan_shares(patient_id,id);
CREATE TABLE medicine_reaction_reports (
 id INTEGER PRIMARY KEY,
 patient_id INTEGER NOT NULL REFERENCES patients(id),
 draft_id INTEGER NOT NULL,
 draft_revision INTEGER NOT NULL,
 drug_code TEXT NOT NULL,
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 author_role TEXT NOT NULL CHECK(author_role IN ('patient','doctor')),
 patient_visible INTEGER NOT NULL CHECK(patient_visible IN (0,1)),
 payload BLOB NOT NULL,
 client_token TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(author_id,client_token),
 FOREIGN KEY(draft_id,draft_revision) REFERENCES medicine_draft_versions(draft_id,revision),
 FOREIGN KEY(draft_id,patient_id) REFERENCES medicine_drafts(id,patient_id)
);
CREATE INDEX medicine_reaction_patient ON medicine_reaction_reports(patient_id,drug_code,id);
CREATE TABLE medicine_reaction_reviews (
 id INTEGER PRIMARY KEY,
 report_id INTEGER NOT NULL REFERENCES medicine_reaction_reports(id),
 author_id INTEGER NOT NULL REFERENCES accounts(id),
 patient_visible INTEGER NOT NULL CHECK(patient_visible IN (0,1)),
 payload BLOB NOT NULL,
 client_token TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(author_id,client_token)
);
CREATE TRIGGER medicine_shares_no_update BEFORE UPDATE ON medicine_plan_shares
 BEGIN SELECT RAISE(ABORT,'immutable shared medicine version'); END;
CREATE TRIGGER medicine_shares_no_delete BEFORE DELETE ON medicine_plan_shares
 BEGIN SELECT RAISE(ABORT,'immutable shared medicine version'); END;
CREATE TRIGGER medicine_reactions_no_update BEFORE UPDATE ON medicine_reaction_reports
 BEGIN SELECT RAISE(ABORT,'immutable medicine reaction report'); END;
CREATE TRIGGER medicine_reactions_no_delete BEFORE DELETE ON medicine_reaction_reports
 BEGIN SELECT RAISE(ABORT,'immutable medicine reaction report'); END;
CREATE TRIGGER medicine_reviews_no_update BEFORE UPDATE ON medicine_reaction_reviews
 BEGIN SELECT RAISE(ABORT,'immutable medicine reaction review'); END;
CREATE TRIGGER medicine_reviews_no_delete BEFORE DELETE ON medicine_reaction_reviews
 BEGIN SELECT RAISE(ABORT,'immutable medicine reaction review'); END;
