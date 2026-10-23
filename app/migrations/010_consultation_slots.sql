CREATE TABLE consultation_slots (
 id INTEGER PRIMARY KEY, doctor_id INTEGER NOT NULL REFERENCES doctors(id),
 created_by INTEGER NOT NULL REFERENCES accounts(id), starts_at TEXT NOT NULL,
 ends_at TEXT NOT NULL, timezone TEXT NOT NULL, url TEXT NOT NULL, fallback TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','booked','cancelled')),
 appointment_id INTEGER REFERENCES appointments(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(ends_at > starts_at)
);
CREATE INDEX consultation_slots_available ON consultation_slots(doctor_id,status,starts_at);
CREATE TABLE consultation_slot_events (
 id INTEGER PRIMARY KEY, slot_id INTEGER NOT NULL REFERENCES consultation_slots(id),
 actor_id INTEGER NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL,
 appointment_id INTEGER REFERENCES appointments(id), created_at TEXT NOT NULL
);
ALTER TABLE appointments ADD COLUMN slot_id INTEGER REFERENCES consultation_slots(id);
ALTER TABLE appointments ADD COLUMN ends_at TEXT;
CREATE UNIQUE INDEX one_active_booking_per_slot ON appointments(slot_id)
 WHERE slot_id IS NOT NULL AND status != 'cancelled';
ALTER TABLE resource_assignments ADD COLUMN cancelled_at TEXT;
ALTER TABLE resource_assignments ADD COLUMN cancelled_by INTEGER REFERENCES accounts(id);
ALTER TABLE resource_events ADD COLUMN payload TEXT NOT NULL DEFAULT '';
