"""Create local synthetic accounts once. Passwords are generated, never stored in source."""

import secrets
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, default_path
from app.security import hash_password


def setup(path=None):
    path = path or default_path()
    credentials = []
    with TestClient(create_app(path)):
        with connect(path) as c:
            if c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
                raise ValueError(
                    "Учётные записи уже настроены. Данные сохранены; используйте существующие пароли или scripts.create_user."
                )
            for username, role, pid in [
                ("specialist", "doctor", None),
                ("patient-a", "patient", 1),
                ("patient-b", "patient", 2),
            ]:
                password = secrets.token_urlsafe(15)
                c.execute(
                    "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,?,?,?)",
                    (username, hash_password(password), role, pid, 1),
                )
                credentials.append((username, password))
    return credentials


if __name__ == "__main__":
    try:
        credentials = setup()
    except ValueError as exc:
        print(exc)
        raise SystemExit(1)
    print(
        "Синтетические локальные аккаунты. Сохраните пароли в личном менеджере: повторно они не выводятся."
    )
    for username, password in credentials:
        print(f"{username}: {password}")
