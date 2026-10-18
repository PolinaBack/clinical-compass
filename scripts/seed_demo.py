"""Populate an empty local database with explicitly synthetic demonstration data."""

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app


def seed(path):
    path = Path(path)
    with TestClient(create_app(path)):
        with sqlite3.connect(path) as c:
            if any(
                c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in [
                    "diary",
                    "messages",
                    "screenings",
                    "relatives",
                    "risks",
                    "decisions",
                    "assessments",
                    "answers",
                    "consents",
                    "audio_notes",
                ]
            ):
                raise ValueError(
                    "Database is not empty. Existing records were preserved."
                )
            for i, score in enumerate([4, 5, 6]):
                day = (date.today() - timedelta(days=2 - i)).isoformat()
                c.execute(
                    "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(?,?,?,?,?,?,?)",
                    (
                        1,
                        day,
                        score,
                        7 + i * 0.5,
                        "none",
                        "Синтетическая демонстрационная запись",
                        0,
                    ),
                )
            today = date.today().isoformat()
            c.execute(
                "INSERT INTO screenings(patient_id,day,tension,difficulty,note) VALUES(?,?,?,?,?)",
                (1, today, 4, 3, "Синтетический самоотчёт; не диагностическая шкала"),
            )
            c.execute(
                "INSERT INTO messages(patient_id,author,body,day) VALUES(?,?,?,?)",
                (
                    1,
                    "Пациент",
                    "Демовопрос: как подготовиться к следующему обсуждению дневника?",
                    today,
                ),
            )
            c.execute(
                "INSERT INTO risks(patient_id,day,kind,body,urgent) VALUES(?,?,?,?,?)",
                (
                    2,
                    today,
                    "event",
                    "Синтетический тест срочного уведомления, не клиническое событие",
                    1,
                ),
            )
            parent = c.execute(
                "INSERT INTO relatives(patient_id,name,relation,parent_id) VALUES(?,?,?,NULL)",
                (1, "Демо-мать", "mother"),
            ).lastrowid
            c.execute(
                "INSERT INTO relatives(patient_id,name,relation,parent_id) VALUES(?,?,?,?)",
                (1, "Демо-пациент А", "self", parent),
            )
    print("Synthetic demo data created.")


if __name__ == "__main__":
    seed(os.getenv("COMPASS_DB", "data/compass.db"))
