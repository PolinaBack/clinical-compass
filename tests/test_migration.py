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


def test_upgrade_v2_preserves_accounts_assessments_and_adds_care_sections(tmp_path):
    from app.storage import connect
    from tests.helpers import login
    from tests.test_workflows import start, save

    path = tmp_path / "v2.db"
    with TestClient(create_app(path)) as client:
        login(client, "patient1")
        aid = start(client)
        assert save(client, aid, [1, 2, 1, 2]).status_code == 200
    # Reconstruct the previously shipped database: new tables had not existed.
    with connect(path) as c:
        old_password_hash = c.execute(
            "SELECT password_hash FROM accounts WHERE username='patient1'"
        ).fetchone()[0]
        for table in [
            "patient_profiles",
            "diary_comments",
            "specialist_reports",
            "specialist_questions",
            "appointments",
            "safety_reviews",
        ]:
            c.execute(f"DROP TABLE {table}")
    for _ in range(2):
        with TestClient(create_app(path)) as client:
            login(client, "patient1")
            assert client.get("/patients/1/profile").status_code == 200
            assert client.get("/patients/1/contact").status_code == 200
        with connect(path) as c:
            assert (
                c.execute(
                    "SELECT password_hash FROM accounts WHERE username='patient1'"
                ).fetchone()[0]
                == old_password_hash
            )
            assert (
                c.execute(
                    "SELECT count(*) FROM answers WHERE assessment_id=?", (aid,)
                ).fetchone()[0]
                == 4
            )
            assert (
                c.execute(
                    "SELECT status FROM assessments WHERE id=?", (aid,)
                ).fetchone()[0]
                == "completed"
            )
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
