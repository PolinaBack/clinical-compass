"""Add one detailed synthetic Абсолют card to an existing database, without reset."""

import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
from datetime import timedelta

from fastapi import HTTPException
from starlette.datastructures import FormData

from app.intake_catalog import BY_KEY, VERSION as INTAKE_VERSION
from app.scales import SCALES, VERSION as SCALE_VERSION
from app.security import hash_password
from app.storage import audit, connect, default_path, now
from app.services import (
    aviandr_data,
    intake,
    medication_preferences,
    medicine_drafts,
    medicine_reactions,
)
from app.services.clinical_forms import today, visible
from scripts import absolut_scenario as scenario
from scripts.backup import backup


def answer_form(fields, values, revision):
    values = {"comment": scenario.NOTICE, "discuss": "Нет", **values}
    answers = {
        key: dict(status="answered", value=value) for key, value in values.items()
    }
    items = [("revision", str(revision)), ("catalog_version", INTAKE_VERSION)]
    for field in fields:
        key = field["key"]
        if not visible(field, answers) or key == "relative_id":
            continue
        # A medication dose is intentionally omitted, never inferred by a seed.
        if key == "dose":
            continue
        if key not in values:
            raise ValueError(f"В синтетическом сценарии не заполнено поле {key}")
        value = values[key]
        items.append(("s_" + key, "answered"))
        items.extend(
            ("a_" + key, str(v))
            for v in (value if isinstance(value, list) else [value])
        )
    return FormData(items)


def seed(c, path, *, doctor_id=1):
    """Caller owns the transaction. Repeats preserve the entire manually edited card."""
    previous = c.execute(
        "SELECT * FROM accounts WHERE username=?", (scenario.USERNAME,)
    ).fetchone()
    if previous:
        profile = c.execute(
            "SELECT * FROM patient_profiles WHERE patient_id=?",
            (previous["patient_id"],),
        ).fetchone()
        if (
            previous["role"] != "patient"
            or not profile
            or scenario.VERSION not in profile["biography"]
        ):
            raise ValueError(
                "Логин patient-absolut уже занят другой картой; ничего не изменено."
            )
        patient = c.execute(
            "SELECT * FROM patients WHERE id=?", (previous["patient_id"],)
        ).fetchone()
        return dict(
            created=False,
            patient_id=patient["id"],
            doctor_id=patient["doctor_id"],
            username=scenario.USERNAME,
        )
    if c.execute("SELECT id FROM patients WHERE name=?", (scenario.NAME,)).fetchone():
        raise ValueError(
            "Карта с именем Абсолют уже существует без метки этого сценария; не объединяем автоматически."
        )
    doctor = c.execute(
        "SELECT * FROM accounts WHERE role='doctor' AND doctor_id=? AND active=1 ORDER BY id LIMIT 1",
        (doctor_id,),
    ).fetchone()
    if not doctor:
        raise ValueError(f"Нет активного специалиста {doctor_id}; карта не создана.")
    end = today()
    begin = end - timedelta(days=29)
    pid = c.execute(
        "INSERT INTO patients(name,doctor_id) VALUES(?,?)", (scenario.NAME, doctor_id)
    ).lastrowid
    password = secrets.token_urlsafe(20)
    aid = c.execute(
        "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,'patient',?,?)",
        (scenario.USERNAME, hash_password(password), pid, doctor_id),
    ).lastrowid
    person = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    c.execute(
        "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(?,?,1,1,?)",
        (pid, scenario.VERSION, now()),
    )
    c.execute(
        "INSERT INTO patient_profiles(patient_id,biography,goals,course,health,stressors,family_context,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            pid,
            scenario.VERSION + " · " + scenario.NOTICE,
            "Уменьшить беспокойство и улучшить сон, сохранив концентрацию",
            "Шесть месяцев жалоб; 30 дней синтетических наблюдений. Колебания не доказывают эффект лекарства.",
            "F41.1 — исходное вымышленное условие, внесённое врачом сценария",
            "Смена работы и дедлайны",
            "Поддержка партнёра; семейные сведения вымышлены",
            now(),
        ),
    )
    for key, values in scenario.categories(end).items():
        intake.save_category(
            c,
            pid,
            person,
            key,
            answer_form(
                BY_KEY[key]["fields"], values, intake.draft(c, pid)["revision"]
            ),
        )
    inn = medicine_drafts.library.current(c, "drug")["D01"]["inn"]
    for kind, values in scenario.entities(begin, end, inn):
        intake.save_entity(
            c,
            pid,
            person,
            kind,
            answer_form(
                intake.entity_fields(c, pid, kind),
                values,
                intake.draft(c, pid)["revision"],
            ),
        )
    c.execute(
        "INSERT INTO relatives(patient_id,name,relation,slot) VALUES(?,?,'mother','mother')",
        (pid, "Вымышленная мать"),
    )
    sid = intake.submit(
        c,
        pid,
        person,
        dict(
            revision=str(intake.draft(c, pid)["revision"]),
            catalog_version=INTAKE_VERSION,
        ),
    )
    intake.review(
        c,
        pid,
        doctor,
        FormData(
            dict(
                submission_id=str(sid),
                target="answer:personal.birth_date",
                status="confirmed",
                note="Дата согласована внутри вымышленного сценария; не верификация реального документа.",
            )
        ),
    )
    facts = [
        ("age", "age", dict(value=36)),
        ("diagnosis", "F41.1", dict(state="present")),
    ]
    facts += [
        ("factor", name, dict(state="absent"))
        for name in (
            "Беременность",
            "Лактация",
            "Заболевания печени",
            "Заболевания почек",
            "Судорожные приступы",
            "Сердечно-сосудистые заболевания",
        )
    ]
    facts.append(("allergy", "Лекарственная аллергия", dict(state="absent")))
    for kind, key, value in facts:
        aviandr_data.add_observation(
            c,
            path,
            doctor,
            pid,
            kind=kind,
            key=key,
            value=value,
            confirmed=True,
            note="Исходное вымышленное условие, проверенное только на согласованность сценария. Не клиническое заключение.",
        )
    start = begin + timedelta(days=10)
    for code, state, day in (
        ("D01", "current", start.isoformat()),
        ("D02", "current", (begin - timedelta(days=60)).isoformat()),
        ("D11", "past", (begin - timedelta(days=90)).isoformat()),
    ):
        aviandr_data.add_observation(
            c,
            path,
            doctor,
            pid,
            kind="therapy",
            key=code,
            value=dict(
                state=state,
                start=day,
                inn=medicine_drafts.library.current(c, "drug")[code]["inn"],
                identity_confirmed=True,
            ),
            confirmed=True,
            note="Учебная сверка: Авиандр и эсциталопрам текущие; Спитомин завершён до окна. Других средств в сценарии нет. Это не рекомендация сочетания: проверка зависит от источников.",
        )
    _observations(c, pid, doctor, begin)
    medication_preferences.save(
        c,
        path,
        person,
        pid,
        FormData(
            [
                ("revision", "0"),
                ("intent", "send"),
                ("primary", "S072"),
                (
                    "comment",
                    "Особенно важно сохранить бодрость на работе; также хочу обсудить тошноту и изменение массы тела. "
                    + scenario.NOTICE,
                ),
                *[("effect", key) for key in scenario.PREFERENCES],
            ]
        ),
    )
    medicine_drafts.save(
        c,
        path,
        doctor,
        pid,
        FormData(
            [
                ("revision", "0"),
                ("drug", "D01"),
                ("D01.dose", "Дозировка в исследовательском сценарии не моделируется"),
                ("D01.frequency", "1 раз в день"),
                ("D01.start", start.isoformat()),
                ("D01.comment", scenario.NOTICE),
            ]
        ),
    )
    draft = c.execute(
        "SELECT id,revision FROM medicine_drafts WHERE patient_id=?", (pid,)
    ).fetchone()
    medicine_reactions.share(
        c, path, doctor, pid, FormData(dict(revision=str(draft["revision"])))
    )
    for i in range(10, 30):
        aviandr_data.add_observation(
            c,
            path,
            person,
            pid,
            kind="intake_event",
            key="D01",
            day=(begin + timedelta(days=i)).isoformat(),
            value=dict(
                draft_id=str(draft["id"]),
                revision=str(draft["revision"]),
                state="missed" if i == 18 else "taken",
                reason="Смена распорядка; вспомнила вечером" if i == 18 else "",
            ),
        )
    rid = medicine_reactions.report(
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
                onset=(begin + timedelta(days=15)).isoformat(),
                impact="mild",
                state="resolved",
                description="Два коротких эпизода после нерегулярного питания, без рвоты; прошли на следующий день. "
                + scenario.NOTICE,
            )
        ),
    )
    medicine_reactions.review(
        c,
        path,
        doctor,
        pid,
        FormData(
            dict(
                report_id=str(rid),
                client_token=secrets.token_urlsafe(24),
                relation="possible",
                assessment_note="Вымышленная оценка: временная связь возможна, одновременно изменилось питание. Причинность не установлена; наблюдение отражено отдельно.",
                patient_visible="yes",
            )
        ),
    )
    c.execute(
        "INSERT INTO diary_notes(patient_id,body,created_at) VALUES(?,?,?)",
        (
            pid,
            "Нагрузка на работе менялась; сон и самочувствие не следует объяснять только терапией. "
            + scenario.NOTICE,
            now(),
        ),
    )
    audit(c, doctor["id"], "synthetic_absolut_created", pid)
    return dict(
        created=True,
        patient_id=pid,
        doctor_id=doctor_id,
        username=scenario.USERNAME,
        password=password,
        period_start=begin.isoformat(),
        period_end=end.isoformat(),
    )


def _observations(c, pid, doctor, begin):
    metric = c.execute(
        "INSERT INTO diary_metrics(patient_id,title,kind,unit,minimum,maximum,created_by,created_at) VALUES(?,'Тревога','scale','0–10',0,10,?,?)",
        (pid, doctor["id"], now()),
    ).lastrowid
    for i in range(30):
        note = scenario.NOTICE
        if i == 15:
            note += " Краткая тошнота и нерегулярное питание."
        if i == 18:
            note += " Один пропуск, изменила распорядок дня."
        if i == 22:
            note += " Дедлайн и поздний отход ко сну."
        did = c.execute(
            "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact,sleep_quality) VALUES(?,?,?,?,?,?,0,?)",
            (
                pid,
                (begin + timedelta(days=i)).isoformat(),
                min(8, 4 + i // 8),
                round(5.5 + i / 20, 1),
                (
                    "До начала"
                    if i < 10
                    else (
                        "Пропуск отмечен отдельно"
                        if i == 18
                        else "Фактический приём отмечен отдельно"
                    )
                ),
                note,
                min(8, 4 + i // 8),
            ),
        ).lastrowid
        c.execute(
            "INSERT INTO diary_metric_values VALUES(?,?,?)",
            (did, metric, str(max(2, 8 - i // 5))),
        )
    for offset, sums in ((0, (8, 16, 12)), (14, (6, 12, 9)), (29, (3, 7, 5))):
        day = begin + timedelta(days=offset)
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
            (pid, (day - timedelta(days=13)).isoformat(), day.isoformat(), now()),
        ).lastrowid
        for scale, total in zip(("phq4", "gad7", "phq9"), sums):
            aid = c.execute(
                "INSERT INTO assessments(session_id,patient_id,scale,version,status,assigned_by,completed_at) VALUES(?,?,?,?,'completed',?,?)",
                (
                    sid,
                    pid,
                    scale,
                    SCALE_VERSION,
                    doctor["id"],
                    day.isoformat() + "T12:00:00+00:00",
                ),
            ).lastrowid
            remaining = total
            for item in range(1, len(SCALES[scale]["items"]) + 1):
                value = 0 if scale == "phq9" and item == 9 else min(3, remaining)
                remaining -= value
                c.execute(
                    "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,?,?,'answered',?)",
                    (aid, str(item), value, now()),
                )


def setup(path=None, *, doctor_id=1):
    path = Path(path or default_path()).resolve()
    if not path.is_file():
        raise ValueError("База не найдена. Укажите существующую compass.db через --db.")
    saved = path.parent / "backups" / ("before-absolut-" + secrets.token_hex(8) + ".db")
    backup(path, saved)
    credentials = None
    try:
        with connect(path) as c:
            c.execute("BEGIN IMMEDIATE")
            result = seed(c, path, doctor_id=doctor_id)
            if (
                c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or c.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError(
                    "Проверка целостности не пройдена; добавление отменено."
                )
            if result.get("password"):
                target = path.parent / (path.name + ".absolut-credentials.json")
                # Write before commit: failure leaves no inaccessible new account.
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                credentials = target
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "username": result["username"],
                            "password": result.pop("password"),
                            "patient_id": result["patient_id"],
                        },
                        stream,
                        ensure_ascii=False,
                        indent=2,
                    )
                result["credentials_file"] = str(credentials)
    except BaseException:
        if credentials is not None:
            credentials.unlink(missing_ok=True)
        raise
    return {**result, "backup": str(saved)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_path())
    parser.add_argument("--doctor-id", type=int, default=1)
    args = parser.parse_args()
    try:
        result = setup(args.db, doctor_id=args.doctor_id)
    except (ValueError, OSError, sqlite3.Error, HTTPException) as exc:
        parser.exit(1, f"Пациент не добавлен: {getattr(exc, 'detail', str(exc))}\n")
    print(
        "Абсолют: "
        + (
            "создана отдельная синтетическая карта"
            if result["created"]
            else "карта уже существует, её данные не изменены"
        )
    )
    print(
        f"Карта {result['patient_id']}, специалист {result['doctor_id']}; /patients/{result['patient_id']}/ai-analysis#aviandr"
    )
    print("Резервная копия:", result["backup"])
    if result.get("credentials_file"):
        print(
            "Данные входа только нового пациента сохранены в приватном файле:",
            result["credentials_file"],
        )
    print(
        "Существующие пациенты, пароли, правила источников и объяснения ИИ не менялись."
    )


if __name__ == "__main__":
    main()
