"""Seed C/D/E idempotently, or explicitly replace a demo DB after a full backup.

--reset is destructive only to the selected live demo database. A verified copy
and its encryption key are retained. Main account passwords are preserved.
Stop the application and every worker before --reset. No startup reset hook.
"""

import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import FormData
from app.main import create_app
from app.storage import connect, default_path, now, audit
from app.security import hash_password
from app.services.clinical_forms import today
from app.services.clinical_attachments import key_path
from app.services import (
    aviandr_data,
    medicine_drafts,
    medicine_reactions,
    medication_preferences,
    intake,
    drug_library,
    adverse_reactions,
    medicine_reference,
)
from app.scales import SCALES, VERSION as SCALE_VERSION
from app.intake_catalog import VERSION as INTAKE_VERSION, BY_KEY
from scripts.backup import backup

VERSION = "aviandr-three-synthetic-scenarios-v1"
WINDOWS = os.name == "nt"


def _seed_historical_scheme(c, path, clinician, pid, form):
    """Import this script's fictional past treatment, never grant library permission.

    Interactive saves and sends must still use their registration/instruction gate.
    This is local scenario provisioning only, with explicitly unverified snapshots.
    """
    medicine_drafts.patient(c, clinician, pid)
    drugs = {
        code: medicine_reference.enrich(d)
        for code, d in drug_library.current(c, "drug").items()
        if not d.get("archived")
    }
    items = medicine_drafts.parse(form, drugs)
    tolerability = adverse_reactions.preference_snapshot(c, pid, path)
    registry = adverse_reactions.records(c)
    for item in items:
        item["preference_matches"] = adverse_reactions.matches(
            registry, item["code"], tolerability
        )
    stamp = now()
    payload = medicine_reactions.pack(
        c, path, dict(items=items, tolerability=tolerability, historical_synthetic=True)
    )
    ident = c.execute(
        "INSERT INTO medicine_drafts(patient_id,author_id,revision,payload,created_at,updated_at) VALUES(?,?,1,?,?,?)",
        (pid, clinician["id"], payload, stamp, stamp),
    ).lastrowid
    c.execute(
        "INSERT INTO medicine_draft_versions VALUES(?,1,?,?)", (ident, payload, stamp)
    )
    shared = medicine_reactions.pack(
        c,
        path,
        dict(
            items=[medicine_reactions.public_item(i) for i in items],
            historical_synthetic=True,
        ),
    )
    c.execute(
        "INSERT INTO medicine_plan_shares(patient_id,draft_id,draft_revision,author_id,payload,created_at) VALUES(?,?,1,?,?,?)",
        (pid, ident, clinician["id"], shared, stamp),
    )
    audit(c, clinician["id"], "synthetic_historical_medicine_seeded", pid, ident)


def reset_failure(message, backup_path):
    archived = (
        f" Полная резервная копия прежней базы: {backup_path}." if backup_path else ""
    )
    return ValueError(message + archived)


def unchanged_database(path, original_hash):
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Появился активный WAL: база используется. Замена отменена.")
    if original_hash is None:
        unchanged = not path.exists()
    else:
        unchanged = (
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
        )
    if not unchanged:
        raise ValueError(
            "База изменилась во время подготовки. Остановите сервер и повторите; замена не выполнена."
        )


def replace_database(fresh, path, original_hash, backup_path):
    """Keep atomic replacement; never delete/unlink a locked destination first."""
    restore = None
    replaced = False
    delays = (0.2, 0.4, 0.8, 1.6)
    try:
        for attempt in range(len(delays) + 1):
            try:
                # Recheck before every retry: a live application may have saved
                # something while Windows temporarily denied the replacement.
                unchanged_database(path, original_hash)
                if WINDOWS and path.exists() and restore is None:
                    metadata = path.stat()
                    if not metadata.st_mode & stat.S_IWRITE:
                        os.chmod(path, metadata.st_mode | stat.S_IWRITE)
                        restore = metadata
                os.replace(fresh, path)
                replaced = True
                return
            except OSError as exc:
                denied = (
                    isinstance(exc, PermissionError)
                    or exc.errno
                    in (
                        errno.EACCES,
                        errno.EPERM,
                        errno.EBUSY,
                    )
                    or getattr(exc, "winerror", None) in (5, 32, 33)
                )
                if denied and attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                if denied:
                    raise reset_failure(
                        f"Не удалось заменить {path}: файл занят или нет прав на замену. "
                        "Прежняя база не заменена. Остановите все Run/Debug-конфигурации "
                        "с uvicorn и ИИ/аудио-обработчиками. В PyCharm закройте вкладки "
                        "просмотра compass.db и отключите соединение в Database "
                        "(Disconnect), затем повторите команду. "
                        "Не удаляйте compass.db и не меняйте права всей папки.",
                        backup_path,
                    ) from None
                raise reset_failure(
                    "ОС не разрешила установить подготовленную базу. Замена не выполнена; "
                    "проверьте доступ к файлу и свободное место.",
                    backup_path,
                ) from None
            except ValueError as exc:
                raise reset_failure(str(exc), backup_path) from None
    finally:
        if restore is not None and not replaced:
            # Restore the read-only flag only on the same original file.
            try:
                current = path.stat()
                if (current.st_dev, current.st_ino) == (restore.st_dev, restore.st_ino):
                    os.chmod(path, restore.st_mode)
            except OSError:
                pass


def account(c, username, role, pid, did, credentials):
    existing = c.execute(
        "SELECT * FROM accounts WHERE role=? AND "
        + ("patient_id=?" if role == "patient" else "doctor_id=? AND active=1")
        + " ORDER BY id LIMIT 1",
        (role, pid if role == "patient" else did),
    ).fetchone()
    if existing:
        return dict(existing)
    if c.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
        raise ValueError(
            f"Логин {username} занят другой ролью или картой. Данные не заменены."
        )
    password = secrets.token_urlsafe(18)
    ident = c.execute(
        "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,?,?,?)",
        (username, hash_password(password), role, pid, did),
    ).lastrowid
    credentials.append((username, password))
    return dict(c.execute("SELECT * FROM accounts WHERE id=?", (ident,)).fetchone())


def seed(c, path, *, reuse_initial=False):
    credentials = []
    for did in (1, 2):
        c.execute(
            "INSERT OR IGNORE INTO doctors VALUES(?,?)",
            (did, f"Синтетический специалист {did}"),
        )
    doctors = {
        did: account(
            c,
            "specialist" if did == 1 else "specialist-2",
            "doctor",
            None,
            did,
            credentials,
        )
        for did in (1, 2)
    }
    created = []
    for offset, letter in enumerate(("C", "D", "E")):
        previous = c.execute(
            "SELECT patient_id FROM aviandr_scenarios WHERE scenario=?", (letter,)
        ).fetchone()
        if previous:
            continue
        did = 2 if letter == "E" else 1
        clinician = doctors[did]
        if reuse_initial:
            pid = offset + 1
            c.execute(
                "INSERT INTO patients(id,name,doctor_id) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,doctor_id=excluded.doctor_id",
                (pid, f"Синтетический пациент {letter}", did),
            )
        else:
            pid = c.execute(
                "INSERT INTO patients(name,doctor_id) VALUES(?,?)",
                (f"Синтетический пациент {letter}", did),
            ).lastrowid
        person = account(
            c, "patient-" + letter.lower(), "patient", pid, did, credentials
        )
        end = today()
        begin = end - timedelta(days=29)
        c.execute(
            "INSERT INTO aviandr_scenarios VALUES(?,?,?,?,?)",
            (letter, pid, VERSION, end.isoformat(), now()),
        )
        c.execute(
            "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(?,'synthetic-research-v1',1,1,?)",
            (pid, now()),
        )
        story = {
            "C": "Тревога перед рабочими обсуждениями и трудности засыпания. Врач рассматривает препарат, текущего приёма нет; опыт переносимости неизвестен.",
            "D": "Тревога, утомляемость, сопутствующая терапия. Точное действующее вещество, аллергологический анамнез и часть сопутствующих факторов ещё не уточнены.",
            "E": "Учебная врачебная схема уже назначена. Пациент ведёт отметки фактического приёма, пропусков, сна и самочувствия; позже сообщает о тошноте. Связь с препаратом не установлена.",
        }[letter]
        c.execute(
            "INSERT INTO patient_profiles(patient_id,biography,goals,course,health,stressors,family_context,updated_at) VALUES(?,?,?,?,'','','',?)",
            (
                pid,
                "Полностью вымышленный исследовательский сценарий " + letter,
                story,
                "30-дневные синтетические наблюдения; не реальные клинические результаты.",
                now(),
            ),
        )
        age = {"C": 32, "D": 47, "E": 39}[letter]
        answers = {
            "personal": {
                "birth_date": dict(status="answered", value=f"{end.year-age}-01-01"),
                "comment": dict(
                    status="answered",
                    value="Полностью вымышленные сведения для технической проверки.",
                ),
            },
            "complaints": {
                "groups": dict(
                    status="answered",
                    value=[BY_KEY["complaints"]["fields"][0]["options"][0]],
                ),
                "onset": dict(
                    status="answered", value="Синтетическая история около месяца"
                ),
            },
        }
        c.execute(
            "INSERT INTO intake_drafts VALUES(?,1,?,?,?)",
            (pid, INTAKE_VERSION, json.dumps(answers, ensure_ascii=False), now()),
        )
        intake.submit(
            c, pid, person, dict(revision="1", catalog_version=INTAKE_VERSION)
        )
        aviandr_data.add_observation(
            c,
            path,
            clinician,
            pid,
            kind="age",
            key="age",
            value=dict(value=age),
            confirmed=True,
            note="Возраст в вымышленном учебном сценарии подтверждён записью исследователя.",
        )
        if letter == "C":
            aviandr_data.add_observation(
                c,
                path,
                clinician,
                pid,
                kind="therapy",
                key="D01",
                value=dict(
                    state="considered",
                    start="",
                    inn=medicine_drafts.library.current(c, "drug")["D01"]["inn"],
                    identity_confirmed=True,
                ),
                confirmed=True,
                note="Вымышленный сценарий: препарат только рассматривается врачом; приём не начат.",
            )
        elif letter == "D":
            aviandr_data.add_observation(
                c,
                path,
                clinician,
                pid,
                kind="therapy",
                key="D11",
                value=dict(state="current", start="", inn="", identity_confirmed=False),
                confirmed=True,
                note="Вымышленная запись о сопутствующей терапии, действующее вещество не подтверждено.",
            )
        else:
            aviandr_data.add_observation(
                c,
                path,
                clinician,
                pid,
                kind="therapy",
                key="D01",
                value=dict(
                    state="current",
                    start=(begin + timedelta(days=14)).isoformat(),
                    inn=medicine_drafts.library.current(c, "drug")["D01"]["inn"],
                    identity_confirmed=True,
                ),
                confirmed=True,
                note="Вымышленная врачебная запись о фактическом начале учебной терапии.",
            )
            aviandr_data.add_observation(
                c,
                path,
                clinician,
                pid,
                kind="diagnosis",
                key="F41.1",
                value=dict(state="present"),
                confirmed=True,
                note="Вымышленный ранее установленный врачом диагноз; не результат вывода модели.",
            )
        metric = c.execute(
            "INSERT INTO diary_metrics(patient_id,title,kind,unit,minimum,maximum,created_by,created_at) VALUES(?,'Тревога','scale','0–10',0,10,?,?)",
            (pid, clinician["id"], now()),
        ).lastrowid
        for day in range(30):
            wellbeing = max(
                0,
                min(
                    10,
                    round((4 if letter != "D" else 3) + day / 15 + (day % 3 - 1) * 0.4),
                ),
            )
            sleep = round(5.4 + day / 24 + (day % 4 - 2) * 0.12, 1)
            note = "Вымышленная ежедневная запись."
            if letter == "E" and day in (18, 24):
                note = "Был пропуск: сменился распорядок дня."
            if letter == "E" and day == 21:
                note = "Сообщаю о тошноте; причина неизвестна."
            if day == 19:
                note += " Нагрузка на работе выросла; это отдельное событие."
            diary_id = c.execute(
                "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact,sleep_quality) VALUES(?,?,?,?,?,?,0,?)",
                (
                    pid,
                    (begin + timedelta(days=day)).isoformat(),
                    wellbeing,
                    sleep,
                    "Не уточнено для отдельных препаратов",
                    note,
                    min(10, 4 + day // 8),
                ),
            ).lastrowid
            c.execute(
                "INSERT INTO diary_metric_values VALUES(?,?,?)",
                (
                    diary_id,
                    metric,
                    str(max(0, 7 - day // 9 + (1 if letter == "D" else 0))),
                ),
            )
        for day in (0, 14, 29):
            period_end = begin + timedelta(days=day)
            sid = c.execute(
                "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
                (
                    pid,
                    (period_end - timedelta(days=13)).isoformat(),
                    period_end.isoformat(),
                    now(),
                ),
            ).lastrowid
            for scale, total in (
                ("phq4", 6 - day // 10),
                ("gad7", 15 - day // 5 + (2 if letter == "D" else 0)),
                ("phq9", 10 - day // 7),
            ):
                aid = c.execute(
                    "INSERT INTO assessments(session_id,patient_id,scale,version,status,assigned_by,completed_at) VALUES(?,?,?,?,'completed',?,?)",
                    (
                        sid,
                        pid,
                        scale,
                        SCALE_VERSION,
                        clinician["id"],
                        period_end.isoformat() + "T12:00:00+00:00",
                    ),
                ).lastrowid
                remaining = total
                for item in range(1, len(SCALES[scale]["items"]) + 1):
                    value = (
                        min(3, remaining) if not (scale == "phq9" and item == 9) else 0
                    )
                    remaining -= value
                    c.execute(
                        "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,?,?,'answered',?)",
                        (aid, str(item), value, now()),
                    )
        choices = ["S014", "S055"] if letter == "E" else ["S072"]
        medication_preferences.assign(c, clinician, pid)
        medication_preferences.save(
            c,
            path,
            person,
            pid,
            FormData(
                [
                    ("revision", "0"),
                    ("intent", "send"),
                    ("primary", choices[0]),
                    (
                        "comment",
                        "Вымышленные пожелания для обсуждения, а не прогноз реакции.",
                    ),
                ]
                + [("effect", v) for v in choices]
            ),
        )
        if letter == "E":
            form = FormData(
                [
                    ("revision", "0"),
                    ("drug", "D01"),
                    (
                        "D01.dose",
                        "Дозировка в исследовательском сценарии не моделируется",
                    ),
                    ("D01.frequency", "1 раз в день"),
                    ("D01.start", (begin + timedelta(days=14)).isoformat()),
                ]
            )
            _seed_historical_scheme(c, path, clinician, pid, form)
            draft = medicine_drafts.read(c, path, clinician, pid)
            draft["id"] = c.execute(
                "SELECT id FROM medicine_drafts WHERE patient_id=? AND author_id=?",
                (pid, clinician["id"]),
            ).fetchone()[0]
            for day in range(14, 30):
                aviandr_data.add_observation(
                    c,
                    path,
                    person,
                    pid,
                    kind="intake_event",
                    key="D01",
                    value=dict(
                        draft_id=str(draft["id"]),
                        revision=str(draft["revision"]),
                        state="missed" if day in (18, 24) else "taken",
                        reason="Изменился распорядок дня" if day in (18, 24) else "",
                    ),
                    day=(begin + timedelta(days=day)).isoformat(),
                )
            medicine_reactions.report(
                c,
                path,
                person,
                pid,
                FormData(
                    dict(
                        draft_id=str(draft["id"]),
                        revision=str(draft["revision"]),
                        code="D01",
                        client_token=secrets.token_urlsafe(24),
                        symptom="other",
                        custom_symptom="Тошнота",
                        onset=(begin + timedelta(days=21)).isoformat(),
                        impact="mild",
                        state="ongoing",
                        description="Вымышленное сообщение пациента. Временное совпадение не подтверждает связь с лечением.",
                    )
                ),
            )
        created.append(dict(scenario=letter, patient_id=pid, doctor_id=did))
    return dict(created=created, credentials=credentials)


def setup(path=None, *, reset=False):
    path = Path(path or default_path()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not reset:
        with TestClient(create_app(path)):
            with connect(path) as c:
                return seed(c, path)
    old = []
    reset_auth_version = 1
    backup_path = None
    original_hash = None
    if path.exists():
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError(
                "Остановите приложение и обработчики: активный WAL препятствует безопасной замене базы."
            )
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with connect(path) as c:
            c.row_factory = sqlite3.Row
            all_old = (
                [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]
                if c.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='accounts'"
                ).fetchone()
                else []
            )
            old = [r for r in all_old if r["active"]]
            reset_auth_version = (
                max([0] + [r.get("auth_version", 0) for r in all_old]) + 1
            )
            if reset_auth_version >= 2**63:
                raise ValueError(
                    "Не удалось безопасно обновить версии сеансов; база не заменена."
                )
        backup_path = (
            path.parent
            / "backups"
            / ("before-aviandr-reset-" + secrets.token_hex(8) + ".db")
        )
        backup(path, backup_path)
    # Only the explicitly requested reset replaces patient records. Default seed
    # is additive; repeats skip already registered scenarios and manual edits.
    with tempfile.TemporaryDirectory(
        prefix=".aviandr-reset-", dir=path.parent, ignore_cleanup_errors=True
    ) as folder:
        fresh = Path(folder) / "fresh.db"
        original_key = key_path(path)
        fresh_key = Path(str(fresh) + ".key")
        if original_key.exists():
            shutil.copyfile(original_key, fresh_key)
            os.chmod(fresh_key, 0o600)
        else:
            from cryptography.fernet import Fernet

            fresh_key.write_bytes(Fernet.generate_key())
            os.chmod(fresh_key, 0o600)
        with TestClient(create_app(fresh)):
            with connect(fresh) as c:
                for did in (1, 2):
                    c.execute(
                        "INSERT OR IGNORE INTO doctors VALUES(?,?)",
                        (did, f"Синтетический специалист {did}"),
                    )
                c.execute(
                    "INSERT OR IGNORE INTO patients VALUES(3,'Синтетический пациент E',2)"
                )
                selected = [r for r in old if r["role"] == "doctor"][:2] + [
                    r for r in old if r["role"] == "patient"
                ][:3]
                doctors = [r for r in selected if r["role"] == "doctor"]
                patients = [r for r in selected if r["role"] == "patient"]
                for role, rows in (("doctor", doctors), ("patient", patients)):
                    for index, row in enumerate(rows, 1):
                        did = index if role == "doctor" else 2 if index == 3 else 1
                        c.execute(
                            "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id,auth_version) VALUES(?,?,?,?,?,?)",
                            (
                                row["username"],
                                row["password_hash"],
                                role,
                                index if role == "patient" else None,
                                did,
                                row.get("auth_version", 0) + 1,
                            ),
                        )
                result = seed(c, fresh, reuse_initial=True)
                c.execute("UPDATE accounts SET auth_version=?", (reset_auth_version,))
                assert (
                    c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    and not c.execute("PRAGMA foreign_key_check").fetchall()
                )
                assert c.execute("SELECT count(*) FROM patients").fetchone()[0] == 3
        try:
            unchanged_database(path, original_hash)
        except (ValueError, OSError) as exc:
            raise reset_failure(
                (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Не удалось проверить исходную базу; замена не выполнена."
                ),
                backup_path,
            ) from None
        if not original_key.exists():
            target = Path(str(path) + ".key")
            shutil.copyfile(fresh_key, target)
            os.chmod(target, 0o600)
        os.chmod(fresh, 0o600)
        replace_database(fresh, path, original_hash, backup_path)
    result["backup"] = str(backup_path) if backup_path else None
    result["preserved_usernames"] = [r["username"] for r in selected] if old else []
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_path())
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Явно заменить демобазу после резервной копии; остановите сервер/worker",
    )
    args = parser.parse_args()
    try:
        result = setup(args.db, reset=args.reset)
        from scripts.reassign_demo_patients import organize

        roster = organize(args.db)
        result["credentials"].extend(roster["credentials"])
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + "\n")
    except OSError:
        parser.exit(
            1,
            "Не удалось подготовить демобазу: проверьте доступ к файлам, свободное место "
            f"и остановите приложение/обработчики. Резервные копии в {args.db.resolve().parent / 'backups'} не удаляйте.\n",
        )
    print("Сценарии C/D/E готовы. Повтор без --reset не перезаписывает наблюдения.")
    if result.get("backup"):
        print("Прежняя база и ключ сохранены:", result["backup"])
    print(
        "A/B закреплены за специалистом 1; C/D — за специалистом 2. E сохранён у прежнего специалиста."
    )
    print("Копия перед исправлением привязок:", roster["backup"])
    for row in roster["assignments"]:
        print(
            f"Пациент {row['patient']}: карта {row['patient_id']}, специалист {row['doctor_id']}, логин {row['username']}"
        )
    if result.get("preserved_usernames"):
        print("Прежние пароли сохранены:", ", ".join(result["preserved_usernames"]))
    if result["credentials"]:
        print(
            "Новые локальные аккаунты; сохраните случайные пароли в личном менеджере:"
        )
        for name, password in result["credentials"]:
            print(f"{name}: {password}")


if __name__ == "__main__":
    main()
