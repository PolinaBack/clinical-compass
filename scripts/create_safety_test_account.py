"""Create one empty patient for testing help links and clinician messages.

Run against the already initialized local application database. Never resets or
modifies an existing account. The generated password is shown only in stdout.
"""

import argparse
import json
import secrets
from app.security import hash_password
from app.storage import connect, default_path, audit


def create_patient(path, username="safety-check", doctor_id=1, actor_id=None):
    if not path.exists():
        raise ValueError("База ещё не создана. Сначала запустите веб-приложение.")
    password = secrets.token_urlsafe(16)
    hashed = hash_password(password)
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
            raise ValueError(
                "Логин уже занят. Для нового чистого пациента укажите --username safety-check-2. Существующий аккаунт не изменён."
            )
        doctor = c.execute(
            "SELECT a.username,d.name FROM accounts a JOIN doctors d ON d.id=a.doctor_id WHERE a.role='doctor' AND a.active=1 AND a.doctor_id=? ORDER BY a.id LIMIT 1",
            (doctor_id,),
        ).fetchone()
        if not doctor:
            raise ValueError(
                "Для указанного --doctor-id не найден активный аккаунт врача."
            )
        pid = c.execute(
            "INSERT INTO patients(name,doctor_id) VALUES(?,?)",
            ("Тестовый пациент · проверка помощи", doctor_id),
        ).lastrowid
        c.execute(
            "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,'patient',?,NULL)",
            (username, hashed, pid),
        )
        audit(c, actor_id, "safety_test_patient_created", pid)
    return dict(
        username=username,
        password=password,
        patient_id=pid,
        doctor_username=doctor["username"],
        doctor_name=doctor["name"],
        patient_url=f"/patients/{pid}/safety",
        messages_url="/doctor/messages",
    )


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="safety-check")
    parser.add_argument("--doctor-id", type=int, default=1)
    parser.add_argument("--db", type=Path, default=default_path())
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 80 or any(c.isspace() for c in args.username):
        parser.error("Логин: 1–80 символов без пробелов")
    try:
        result = create_patient(args.db, args.username, args.doctor_id)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
