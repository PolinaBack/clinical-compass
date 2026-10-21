ALTER TABLE diary ADD COLUMN sleep_quality INTEGER CHECK(sleep_quality BETWEEN 0 AND 10);
CREATE TABLE diary_metrics (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patients(id),
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 100),
    kind TEXT NOT NULL CHECK(kind IN ('scale','number','boolean','text')),
    unit TEXT NOT NULL DEFAULT '',
    minimum REAL,
    maximum REAL,
    created_by INTEGER NOT NULL REFERENCES accounts(id),
    created_at TEXT NOT NULL,
    CHECK(minimum IS NULL OR maximum IS NULL OR minimum <= maximum)
);
CREATE INDEX diary_metrics_patient ON diary_metrics(patient_id,id);
CREATE TABLE diary_metric_values (
    diary_id INTEGER NOT NULL REFERENCES diary(id),
    metric_id INTEGER NOT NULL REFERENCES diary_metrics(id),
    value TEXT NOT NULL,
    PRIMARY KEY(diary_id,metric_id)
);
