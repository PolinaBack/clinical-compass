"""Restore separate demo A/B cards and assign C/D to specialist 2, without reset."""

import argparse
from pathlib import Path
import secrets
import sqlite3

from app.security import hash_password
from app.storage import connect, default_path
from scripts.backup import backup


def organize(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("База не найдена. Укажите существующую compass.db через --db.")
    backup_path = (
        path.parent / "backups" / ("before-demo-roster-" + secrets.token_hex(8) + ".db")
    )
    backup(path, backup_path)
    credentials, assignments = [], []
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        for did, name in ((1, "specialist"), (2, "specialist-2")):
            user = c.execute(
                "SELECT * FROM accounts WHERE role='doctor' AND doctor_id=? AND active=1 ORDER BY CASE WHEN username=? THEN 0 ELSE 1 END,id LIMIT 1",
                (did, name),
            ).fetchone()
            if not user:
                raise ValueError(
                    f"Нет активного специалиста {did}; изменения не выполнены."
                )
        scenarios = {
            row["scenario"]: row["patient_id"]
            for row in c.execute("SELECT * FROM aviandr_scenarios")
        }
        if not {"C", "D"}.issubset(scenarios):
            raise ValueError(
                "Сначала создайте синтетические сценарии C/D. Обычные карты не переименованы."
            )
        scenario_ids = set(scenarios.values())
        for letter, russian in (("A", "А"), ("B", "Б")):
            username = "patient-" + letter.lower()
            account = c.execute(
                "SELECT * FROM accounts WHERE username=?", (username,)
            ).fetchone()
            if account and account["role"] != "patient":
                raise ValueError(
                    f"Логин {username} занят другой ролью; изменения отменены."
                )
            pid = (
                account["patient_id"]
                if account and account["patient_id"] not in scenario_ids
                else None
            )
            if not pid:
                aliases = {
                    f"{prefix} {suffix}".casefold()
                    for prefix in (
                        "Синтетический пациент",
                        "Демо-пациент",
                        "Учебный пациент",
                        "Пациент",
                    )
                    for suffix in (letter, russian)
                }
                matches = [
                    row["id"]
                    for row in c.execute("SELECT id,name FROM patients")
                    if row["id"] not in scenario_ids
                    and row["name"].casefold() in aliases
                ]
                if len(matches) > 1:
                    raise ValueError(
                        f"Найдено несколько карт {letter}; автоматическое объединение не выполнено."
                    )
                pid = matches[0] if matches else None
            if not pid:
                pid = c.execute(
                    "INSERT INTO patients(name,doctor_id) VALUES(?,1)",
                    ("Синтетический пациент " + letter,),
                ).lastrowid
            owner = c.execute(
                "SELECT * FROM accounts WHERE role='patient' AND patient_id=?", (pid,)
            ).fetchone()
            if owner and not account:
                account = owner
                username = owner["username"]
            if owner and (not account or owner["id"] != account["id"]):
                raise ValueError(
                    f"У карты {letter} уже другой аккаунт; изменения отменены."
                )
            if account:
                changed = account["patient_id"] != pid
                c.execute(
                    "UPDATE accounts SET patient_id=?,doctor_id=1,auth_version=auth_version+? WHERE id=?",
                    (pid, int(changed), account["id"]),
                )
            else:
                password = secrets.token_urlsafe(18)
                c.execute(
                    "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,'patient',?,1)",
                    (username, hash_password(password), pid),
                )
                credentials.append((username, password))
            c.execute(
                "UPDATE patients SET doctor_id=1 WHERE id=? AND doctor_id!=1", (pid,)
            )
            assignments.append(
                dict(patient=letter, patient_id=pid, doctor_id=1, username=username)
            )
        for letter in ("C", "D"):
            pid = scenarios[letter]
            owner = c.execute(
                "SELECT * FROM accounts WHERE role='patient' AND patient_id=?", (pid,)
            ).fetchone()
            if not owner:
                username = "patient-" + letter.lower()
                if c.execute(
                    "SELECT id FROM accounts WHERE username=?", (username,)
                ).fetchone():
                    raise ValueError(
                        f"Логин {username} уже связан с другой картой; изменения отменены."
                    )
                password = secrets.token_urlsafe(18)
                c.execute(
                    "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,'patient',?,2)",
                    (username, hash_password(password), pid),
                )
                credentials.append((username, password))
            else:
                username = owner["username"]
                c.execute(
                    "UPDATE accounts SET doctor_id=2 WHERE id=? AND doctor_id!=2",
                    (owner["id"],),
                )
            c.execute(
                "UPDATE patients SET doctor_id=2 WHERE id=? AND doctor_id!=2", (pid,)
            )
            assignments.append(
                dict(patient=letter, patient_id=pid, doctor_id=2, username=username)
            )
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    return dict(
        assignments=assignments, credentials=credentials, backup=str(backup_path)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_path())
    args = parser.parse_args()
    try:
        result = organize(args.db)
    except (ValueError, sqlite3.Error, OSError) as exc:
        parser.exit(1, f"Привязки не изменены: {exc}\n")
    print("A/B — специалист 1; C/D — специалист 2. Истории и прежние пароли сохранены.")
    print("Резервная копия:", result["backup"])
    for row in result["assignments"]:
        print(
            f"{row['patient']}: карта {row['patient_id']}, специалист {row['doctor_id']}, логин {row['username']}"
        )
    if result["credentials"]:
        print("Пароли только новых аккаунтов — сохраните в личном менеджере:")
        for name, password in result["credentials"]:
            print(f"{name}: {password}")
    print(
        "Если patient-a/b раньше открывали C/D, выйдите и войдите снова: их старые сеансы отозваны при смене карты."
    )


if __name__ == "__main__":
    main()
