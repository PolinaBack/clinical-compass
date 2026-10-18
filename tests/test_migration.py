import sqlite3
from fastapi.testclient import TestClient
from app.main import create_app


def test_additive_upgrade_preserves_existing_v1_diary_and_creates_no_default_password(
    tmp_path,
):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.executescript(
            """
        CREATE TABLE doctors(id INTEGER PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE patients(id INTEGER PRIMARY KEY,name TEXT NOT NULL,doctor_id INTEGER NOT NULL DEFAULT 1 REFERENCES doctors(id));
        CREATE TABLE diary(id INTEGER PRIMARY KEY,patient_id INTEGER REFERENCES patients(id),day TEXT NOT NULL,wellbeing INTEGER NOT NULL,sleep REAL NOT NULL,adherence TEXT NOT NULL,note TEXT NOT NULL,contact INTEGER NOT NULL);
        INSERT INTO doctors VALUES(1,'Синтетический врач');
        INSERT INTO patients VALUES(77,'Синтетический старый профиль',1);
        INSERT INTO diary VALUES(42,77,'2026-10-01',5,8,'none','Сохранённая синтетическая запись',0);
        """
        )
    for _ in range(2):
        with TestClient(create_app(path)) as client:
            assert client.get("/").status_code == 200
        with sqlite3.connect(path) as c:
            assert (
                c.execute("SELECT note FROM diary WHERE id=42").fetchone()[0]
                == "Сохранённая синтетическая запись"
            )
            assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
