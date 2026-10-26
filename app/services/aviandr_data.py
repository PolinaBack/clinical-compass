"""Source-labelled synthetic-card data; private drafts never establish treatment."""

import secrets
from datetime import date, timedelta
from fastapi import HTTPException
from app.storage import now, audit
from app.experimental_ai.scenarios import digest
from app.services.clinical_forms import today
from app.services.patient_ai_data import scope, text, numeric
from app.services import (
    medicine_reactions as medicines,
    adverse_reactions,
    drug_library,
)
from app.services.intake import read_submission
from app.scales import SCALES, score

KINDS = {
    "age": "Возраст",
    "diagnosis": "Установленный диагноз",
    "factor": "Сопутствующее заболевание / фактор",
    "allergy": "Аллергия / непереносимость",
    "therapy": "Приём препарата",
}
STATES = {"present": "Есть", "absent": "Отрицается", "unknown": "Неизвестно"}
THERAPY = {
    "current": "Принимает сейчас",
    "past": "Принимал(а) ранее",
    "considered": "Рассматривается врачом",
    "unknown": "Не уточнено",
}


def add_observation(
    c,
    path,
    user,
    pid,
    *,
    kind,
    key,
    value,
    confirmed=False,
    note="",
    day=None,
    token=None,
):
    if user["role"] == "patient" and (
        user["patient_id"] != pid or kind != "intake_event"
    ):
        raise HTTPException(403, "Нет доступа к записи этой карты")
    (
        scope(c, user["id"], pid, user["auth_version"])
        if user["role"] == "doctor"
        else None
    )
    day = day or today().isoformat()
    try:
        if date.fromisoformat(day) > today() or not isinstance(value, dict):
            raise ValueError()
        if (
            not isinstance(key, str)
            or not 1 <= len(key.strip()) <= 200
            or len(note) > 2000
        ):
            raise ValueError()
        if kind == "intake_event":
            medicines.source(
                c, path, user, pid, int(value["draft_id"]), int(value["revision"]), key
            )
            if (
                value.get("state") not in {"taken", "missed", "unknown"}
                or len(value.get("reason", "")) > 1000
            ):
                raise ValueError()
            confirmed = False
        else:
            if user["role"] != "doctor" or kind not in KINDS or len(note.strip()) < 8:
                raise ValueError()
            if kind == "age":
                age = int(value.get("value", ""))
                if not 0 <= age <= 130:
                    raise ValueError()
                value = dict(value=age)
                key = "age"
            elif kind == "therapy":
                if (
                    key not in drug_library.current(c, "drug")
                    or value.get("state") not in THERAPY
                ):
                    raise ValueError()
                start = value.get("start", "")
                if start and date.fromisoformat(start) > today():
                    raise ValueError()
                value = dict(
                    state=value["state"],
                    start=start,
                    inn=str(value.get("inn", ""))[:200],
                    identity_confirmed=value.get("identity_confirmed") is True,
                )
            elif value.get("state") not in STATES:
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(
            422,
            "Проверьте вид записи, значение, дату и основание. Исходные записи сохранены.",
        ) from None
    payload = dict(key=key.strip(), value=value, note=note.strip())
    token = token or secrets.token_urlsafe(24)
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise HTTPException(422, "Обновите форму записи")
    prior = c.execute(
        "SELECT * FROM aviandr_observations WHERE actor_id=? AND client_token=?",
        (user["id"], token),
    ).fetchone()
    if prior:
        if (
            prior["patient_id"] != pid
            or prior["kind"] != kind
            or bool(prior["confirmed"]) != bool(confirmed)
            or prior["observed_on"] != day
            or medicines.unpack(c, path, prior["payload"]) != payload
        ):
            raise HTTPException(
                409, "Эта форма уже сохранена с другими данными. Откройте новую форму."
            )
        return prior["id"]
    ident = c.execute(
        "INSERT INTO aviandr_observations(patient_id,actor_id,kind,fact_key,confirmed,payload,source_note,observed_on,client_token,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            pid,
            user["id"],
            kind,
            digest([kind, key, day if kind == "intake_event" else ""]),
            int(confirmed),
            medicines.pack(c, path, payload),
            "Запись врача" if user["role"] == "doctor" else "Самоотчёт пациента",
            day,
            token,
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "aviandr_observation_saved", pid, ident)
    return ident


def observations(c, path, pid):
    latest = {}
    for row in c.execute(
        "SELECT * FROM aviandr_observations WHERE patient_id=? ORDER BY id DESC", (pid,)
    ):
        value = medicines.unpack(c, path, row["payload"])
        identity = (
            row["kind"],
            value["key"],
            row["observed_on"] if row["kind"] == "intake_event" else "",
        )
        latest.setdefault(
            identity,
            dict(
                id=row["id"],
                kind=row["kind"],
                confirmed=bool(row["confirmed"]),
                source=row["source_note"],
                day=row["observed_on"],
                **value,
            ),
        )
    return list(latest.values())


def collect(c, path, user, pid):
    scope(c, user["id"], pid, user["auth_version"])
    obs = observations(c, path, pid)
    facts = []
    for item in obs:
        facts.append(
            dict(
                id=f"O{item['id']}",
                kind=item["kind"],
                key=item["key"],
                value=item["value"],
                confirmed=item["confirmed"],
                source=item["source"],
                day=item["day"],
                note=text(item["note"], 700),
            )
        )
    submission = read_submission(c, pid)
    reported = []
    if submission:
        raw = submission["data"]
        for section in (
            "complaints",
            "mental",
            "health",
            "medicines",
            "reproductive",
        ):
            sex = raw.get("answers", {}).get("personal", {}).get("sex", {})
            if section == "reproductive" and (
                sex.get("status") != "answered" or sex.get("value") != "Женский"
            ):
                continue
            answers = raw.get("answers", {}).get(section, {})
            if answers:
                reported.append(
                    dict(
                        section=section,
                        answers=answers,
                        source=f"Отправленный анамнез №{submission['id']}",
                        confirmed=False,
                    )
                )
        born = raw.get("answers", {}).get("personal", {}).get("birth_date", {})
        if born.get("status") == "answered":
            try:
                birthday = date.fromisoformat(born["value"])
                if birthday <= today():
                    age = (
                        today().year
                        - birthday.year
                        - (
                            (today().month, today().day)
                            < (birthday.month, birthday.day)
                        )
                    )
                    age_review = c.execute(
                        "SELECT status FROM intake_reviews WHERE submission_id=? AND target='answer:personal.birth_date' ORDER BY id DESC LIMIT 1",
                        (submission["id"],),
                    ).fetchone()
                    age_confirmed = bool(
                        age_review and age_review["status"] == "confirmed"
                    )
                    facts.append(
                        dict(
                            id=f"I{submission['id']}-age",
                            kind="age",
                            key="age",
                            value=dict(value=age),
                            confirmed=age_confirmed,
                            source="Дата рождения из отправленной анкеты",
                            day=submission["created_at"][:10],
                            note=(
                                "Дата рождения проверена врачом"
                                if age_confirmed
                                else "Со слов пациента"
                            ),
                        )
                    )
            except (KeyError, TypeError, ValueError):
                pass
        for entity in raw.get("entities", []):
            if entity.get("kind") in {"condition", "medication", "reaction"}:
                reported.append(
                    dict(
                        kind=entity["kind"],
                        data=entity.get("data", {}),
                        source=f"Отправленный анамнез №{submission['id']}",
                        confirmed=False,
                    )
                )
                values = entity.get("data", {})

                def answered(key):
                    field = values.get(key, {})
                    return (
                        str(field.get("value", ""))
                        if isinstance(field, dict) and field.get("status") == "answered"
                        else ""
                    )

                name = answered("name")
                review = c.execute(
                    "SELECT status FROM intake_reviews WHERE submission_id=? AND target=? ORDER BY id DESC LIMIT 1",
                    (submission["id"], f"entity:{entity['id']}:{entity['version']}"),
                ).fetchone()
                confirmed = bool(review and review["status"] == "confirmed")
                if name and entity["kind"] == "condition":
                    for kind in ("factor", "diagnosis"):
                        if not any(
                            f["kind"] == kind and f["key"].casefold() == name.casefold()
                            for f in facts
                        ):
                            facts.append(
                                dict(
                                    id=f"I{submission['id']}-E{entity['id']}-{kind}",
                                    kind=kind,
                                    key=name,
                                    value=dict(state="present"),
                                    confirmed=confirmed,
                                    source=f"Отправленная карточка заболевания №{entity['id']}"
                                    + (
                                        " · проверена врачом"
                                        if confirmed
                                        else " · со слов"
                                    ),
                                    day=submission["created_at"][:10],
                                    note="",
                                )
                            )
                if (
                    name
                    and entity["kind"] == "reaction"
                    and answered("type") == "Аллергия"
                    and not any(
                        f["kind"] == "allergy"
                        and f["key"].casefold() == name.casefold()
                        for f in facts
                    )
                ):
                    facts.append(
                        dict(
                            id=f"I{submission['id']}-E{entity['id']}-allergy",
                            kind="allergy",
                            key=name,
                            value=dict(state="present"),
                            confirmed=confirmed,
                            source=f"Отправленная карточка реакции №{entity['id']}",
                            day=submission["created_at"][:10],
                            note=answered("description"),
                        )
                    )
    questionnaires = []
    for a in c.execute(
        "SELECT a.*,s.period_start,s.period_end FROM assessments a JOIN assessment_sessions s ON s.id=a.session_id WHERE a.patient_id=? ORDER BY s.period_end,a.id",
        (pid,),
    ):
        if a["scale"] not in SCALES:
            continue
        answers = {
            r["item"]: dict(r)
            for r in c.execute(
                "SELECT * FROM answers WHERE assessment_id=?", (a["id"],)
            )
        }
        result = score(a["scale"], answers)
        questionnaires.append(
            dict(
                id=a["id"],
                scale=a["scale"],
                version=a["version"],
                status=a["status"],
                period_start=a["period_start"],
                period_end=a["period_end"],
                total=result["total"] if a["status"] == "completed" else None,
                answered=result["answered"],
                count=result["count"],
                answers=[
                    dict(
                        item=k,
                        value=v["value"],
                        state=v["state"],
                        comment=text(v["comment"], 300),
                    )
                    for k, v in answers.items()
                ],
            )
        )
    # A snapshot of a shared prescription documents a plan, never proof of taking it.
    shared = []
    plan = c.execute(
        "SELECT * FROM medicine_plan_shares WHERE patient_id=? ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()
    if plan:
        value = medicines.unpack(c, path, plan["payload"])
        shared = dict(
            id=plan["id"], created_at=plan["created_at"], items=value.get("items", [])
        )
    drugs = {d["code"]: d for d in drug_library.search(c, include_archived=True)}
    therapy = []
    for f in facts:
        if f["kind"] == "therapy":
            d = drugs.get(f["key"], {})
            therapy.append(
                dict(
                    code=f["key"],
                    name=d.get("name", f["key"]),
                    **f["value"],
                    confirmed=f["confirmed"],
                    source=f["id"],
                )
            )
    if submission:
        for entity in submission["data"].get("entities", []):
            if entity.get("kind") != "medication":
                continue
            fields = entity.get("data", {})

            def med_value(key):
                field = fields.get(key, {})
                return (
                    str(field.get("value", ""))
                    if isinstance(field, dict) and field.get("status") == "answered"
                    else ""
                )

            name = med_value("name")
            category = med_value("category")
            if not name or category in {"Принимал(а) раньше", "Недавно отменено"}:
                continue
            identified = [
                d
                for d in drugs.values()
                if drug_library.normalize(name)
                in {drug_library.normalize(d["name"]), drug_library.normalize(d["inn"])}
            ]
            code = (
                identified[0]["code"]
                if len(identified) == 1
                else f"unidentified-{entity['id']}"
            )
            if not any(t["code"] == code for t in therapy):
                review = c.execute(
                    "SELECT status FROM intake_reviews WHERE submission_id=? AND target=? ORDER BY id DESC LIMIT 1",
                    (submission["id"], f"entity:{entity['id']}:{entity['version']}"),
                ).fetchone()
                verified = bool(review and review["status"] == "confirmed")
                therapy.append(
                    dict(
                        code=code,
                        name=name,
                        inn=med_value("ingredient"),
                        state=(
                            "current"
                            if verified
                            and category
                            in {
                                "Назначено, принимаю сейчас",
                                "Фактически принимаю иначе",
                            }
                            else "unknown"
                        ),
                        start=med_value("started"),
                        confirmed=verified,
                        identity_confirmed=verified,
                        source=f"Отправленная карточка лекарства №{entity['id']}"
                        + (
                            " · проверено врачом" if verified else " · со слов пациента"
                        ),
                    )
                )
    if shared:
        for item in shared["items"]:
            if not any(t["code"] == item["code"] for t in therapy):
                therapy.append(
                    dict(
                        code=item["code"],
                        name=item.get("reference", {}).get("name", item["code"]),
                        inn=item.get("reference", {}).get("inn", ""),
                        state="prescribed",
                        start=item.get("start", ""),
                        confirmed=False,
                        identity_confirmed=False,
                        source=f"Учебная схема №{shared['id']}, фактический приём не уточнён",
                    )
                )
    for therapy_item in therapy:
        known = drugs.get(therapy_item["code"])
        therapy_item["identity_consistent"] = bool(
            known
            and drug_library.normalize(therapy_item.get("inn", ""))
            == drug_library.normalize(known["inn"])
        )
        therapy_item["identity_confirmed"] = bool(
            therapy_item.get("identity_confirmed")
            and therapy_item["identity_consistent"]
        )
    end = today()
    start = end - timedelta(days=29)
    diary = [
        dict(r)
        for r in c.execute(
            "SELECT id,day,wellbeing,sleep,sleep_quality,adherence,note FROM diary WHERE patient_id=? AND day BETWEEN ? AND ? ORDER BY day,id",
            (pid, start.isoformat(), end.isoformat()),
        )
    ]
    latest_days = {r["day"]: r for r in diary}
    daily_points = []
    for offset in range(30):
        day = (start + timedelta(days=offset)).isoformat()
        row = latest_days.get(day, {})
        daily_points.append(
            dict(
                day=day,
                diary_id=row.get("id"),
                wellbeing=numeric(row.get("wellbeing")),
                sleep_hours=numeric(row.get("sleep"), 24),
                sleep_quality=numeric(row.get("sleep_quality")),
                anxiety=None,
                anxiety_metric_id=None,
            )
        )
    # Reuse the existing patient-card convention for an explicitly named
    # subjective anxiety scale. No diagnostic interpretation or missing=zero.
    by_day = {r["day"]: r for r in daily_points}
    for row in c.execute(
        "SELECT d.day,d.id diary_id,m.id metric_id,m.title,m.kind,m.minimum,m.maximum,v.value "
        "FROM diary_metric_values v JOIN diary d ON d.id=v.diary_id "
        "JOIN diary_metrics m ON m.id=v.metric_id "
        "WHERE d.patient_id=? AND m.patient_id=? AND d.day BETWEEN ? AND ? "
        "ORDER BY d.day DESC,d.id DESC,m.id",
        (pid, pid, start.isoformat(), end.isoformat()),
    ):
        point = by_day[row["day"]]
        if (
            point["diary_id"] == row["diary_id"]
            and point["anxiety_metric_id"] is None
            and "тревог" in row["title"].casefold()
            and row["kind"] == "scale"
            and row["minimum"] == 0
            and row["maximum"] == 10
        ):
            point["anxiety"] = numeric(row["value"])
            point["anxiety_metric_id"] = row["metric_id"]
    reports = medicines.listing(c, path, user, pid)
    preferences = adverse_reactions.preference_snapshot(c, pid, path)
    profile = c.execute(
        "SELECT * FROM patient_profiles WHERE patient_id=?", (pid,)
    ).fetchone()
    return dict(
        facts=facts,
        intake_submission=(
            dict(
                id=submission["id"],
                revision=submission["revision"],
                created_at=submission["created_at"],
            )
            if submission
            else None
        ),
        reported=reported,
        profile=(
            {
                k: text(profile[k], 1500)
                for k in (
                    "biography",
                    "goals",
                    "course",
                    "health",
                    "stressors",
                    "family_context",
                )
            }
            if profile
            else {}
        ),
        questionnaires=questionnaires,
        therapy=therapy,
        shared=shared,
        diary=diary,
        observations=daily_points,
        reactions=reports,
        preferences=preferences,
        preference_choices=adverse_reactions.preference_choices(preferences),
        period_start=start.isoformat(),
        period_end=end.isoformat(),
    )
