"""Create a local account or reset its password interactively. No passwords in argv."""

import argparse
import getpass
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, default_path
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=["patient", "doctor"])
    parser.add_argument("--patient-id", type=int)
    parser.add_argument("--doctor-id", type=int, default=1)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 80:
        parser.error("Недопустимое имя")
    password = getpass.getpass("Новый пароль (10–256 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        parser.error("Пароли не совпадают")
    hashed = hash_password(password)
    path = default_path()
    with TestClient(create_app(path)):
        with connect(path) as c:
            if args.reset:
                if not c.execute(
                    "SELECT id FROM accounts WHERE username=?", (args.username,)
                ).fetchone():
                    parser.error("Аккаунт не найден")
                c.execute(
                    "UPDATE accounts SET password_hash=?,auth_version=auth_version+1 WHERE username=?",
                    (hashed, args.username),
                )
            else:
                if not args.role:
                    parser.error("Нужен --role")
                if args.role == "patient" and not args.patient_id:
                    parser.error("Нужен --patient-id")
                if not c.execute(
                    "SELECT id FROM doctors WHERE id=?", (args.doctor_id,)
                ).fetchone():
                    c.execute(
                        "INSERT INTO doctors VALUES(?,?)",
                        (args.doctor_id, f"Синтетический специалист {args.doctor_id}"),
                    )
                c.execute(
                    "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,?,?,?)",
                    (
                        args.username,
                        hashed,
                        args.role,
                        args.patient_id if args.role == "patient" else None,
                        args.doctor_id,
                    ),
                )
    print("Аккаунт обновлён." if args.reset else "Аккаунт создан.")


if __name__ == "__main__":
    main()
