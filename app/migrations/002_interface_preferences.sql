CREATE TABLE interface_preferences (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
    minimal INTEGER NOT NULL DEFAULT 0 CHECK(minimal IN (0,1)),
    cards_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
