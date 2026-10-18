"""Additive schema for the existing local MVP; no user data is removed."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def default_path():
    return Path(
        os.getenv(
            "COMPASS_DB",
            str(Path(__file__).resolve().parent.parent / "data" / "compass.db"),
        )
    )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def extend_schema(c):
    c.executescript(
        """
    CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('doctor','patient')), patient_id INTEGER UNIQUE REFERENCES patients(id), doctor_id INTEGER REFERENCES doctors(id), active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS consents(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), version TEXT NOT NULL, accepted INTEGER NOT NULL, adult INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, account_id INTEGER, action TEXT NOT NULL, patient_id INTEGER, object_id INTEGER, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assessment_sessions(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), period_start TEXT NOT NULL, period_end TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES assessment_sessions(id), patient_id INTEGER NOT NULL REFERENCES patients(id), scale TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', assigned_by INTEGER REFERENCES accounts(id), due_day TEXT, completed_at TEXT, seen_at TEXT, revision INTEGER NOT NULL DEFAULT 0, UNIQUE(session_id,scale));
    CREATE TABLE IF NOT EXISTS answers(id INTEGER PRIMARY KEY, assessment_id INTEGER NOT NULL REFERENCES assessments(id), item TEXT NOT NULL, value INTEGER, state TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', discuss INTEGER NOT NULL DEFAULT 0, discussed INTEGER NOT NULL DEFAULT 0, changed_at TEXT NOT NULL, UNIQUE(assessment_id,item));
    CREATE TABLE IF NOT EXISTS answer_history(id INTEGER PRIMARY KEY, answer_id INTEGER NOT NULL REFERENCES answers(id), payload TEXT NOT NULL, account_id INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assessment_events(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), assessment_id INTEGER NOT NULL REFERENCES assessments(id), kind TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT, action_text TEXT, action_at TEXT, UNIQUE(assessment_id,kind));
    CREATE TABLE IF NOT EXISTS audio_notes(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), assessment_id INTEGER NOT NULL REFERENCES assessments(id), item TEXT NOT NULL, file_name TEXT NOT NULL, mime TEXT NOT NULL, duration REAL NOT NULL, consent_at TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '', transcript_status TEXT NOT NULL DEFAULT 'not_transcribed', created_at TEXT NOT NULL, deleted_at TEXT, content BLOB);
    CREATE TABLE IF NOT EXISTS save_requests(token TEXT PRIMARY KEY, account_id INTEGER NOT NULL, payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS diary_patient ON diary(patient_id);
    CREATE INDEX IF NOT EXISTS messages_patient ON messages(patient_id);
    CREATE INDEX IF NOT EXISTS risks_patient ON risks(patient_id);
    CREATE INDEX IF NOT EXISTS assessments_patient ON assessments(patient_id);
    CREATE INDEX IF NOT EXISTS events_patient ON assessment_events(patient_id);
    CREATE INDEX IF NOT EXISTS audit_patient ON audit(patient_id);
    """
    )
    account_columns = {r["name"] for r in c.execute("PRAGMA table_info(accounts)")}
    if "auth_version" not in account_columns:
        c.execute(
            "ALTER TABLE accounts ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0"
        )
    audio_columns = {r["name"] for r in c.execute("PRAGMA table_info(audio_notes)")}
    if "content" not in audio_columns:
        c.execute("ALTER TABLE audio_notes ADD COLUMN content BLOB")


def audit(c, account_id, action, patient_id=None, object_id=None):
    c.execute(
        "INSERT INTO audit(account_id,action,patient_id,object_id,created_at) VALUES(?,?,?,?,?)",
        (account_id, action, patient_id, object_id, now()),
    )
