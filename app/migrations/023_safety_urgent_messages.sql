CREATE TABLE safety_urgent_snapshots (
 event_id INTEGER PRIMARY KEY REFERENCES assessment_events(id),
 payload TEXT NOT NULL
);
CREATE TRIGGER safety_urgent_snapshot_no_update BEFORE UPDATE ON safety_urgent_snapshots
BEGIN SELECT RAISE(ABORT,'Safety trigger history is immutable'); END;
