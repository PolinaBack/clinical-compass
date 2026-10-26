CREATE TABLE diary_card_templates (
 id INTEGER PRIMARY KEY,
 owner_id INTEGER NOT NULL REFERENCES accounts(id),
 definition TEXT NOT NULL,
 client_token TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(owner_id,client_token)
);
CREATE INDEX diary_card_templates_owner ON diary_card_templates(owner_id,id);
