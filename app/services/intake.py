"""Versioned patient drafts, immutable submissions and clinician fact review."""

import json
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from fastapi import HTTPException
from app.intake_catalog import (
    VERSION,
    CATEGORIES,
    BY_KEY,
    ENTITY_FIELDS,
    ENTITY_CATEGORY,
    ENTITY_LABELS,
    STATES,
    MEASUREMENT_KEYS,
)
from app.services.clinical_forms import (
    parse_answers,
    visible,
    display,
    display_field,
    today,
)
from app.storage import now, audit


def check_scope(c, user, pid, write=False, doctor=False):
    a = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (user["id"],)
    ).fetchone()
    p = c.execute("SELECT doctor_id FROM patients WHERE id=?", (pid,)).fetchone()
    if (
        not a
        or a["auth_version"] != user["auth_version"]
        or not p
        or not (
            (a["role"] == "patient" and a["patient_id"] == pid)
            or (a["role"] == "doctor" and a["doctor_id"] == p["doctor_id"])
        )
        or write
        and a["role"] != "patient"
        or doctor
        and a["role"] != "doctor"
    ):
        raise HTTPException(403, "Нет доступа к этой анкете")
    if write:
        consent = c.execute(
            "SELECT accepted,adult FROM consents WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        if not consent or not consent["accepted"] or not consent["adult"]:
            raise HTTPException(403, "Сначала подтвердите участие на странице согласия")


def preferences(c, pid):
    row = c.execute(
        "SELECT * FROM intake_preferences WHERE patient_id=?", (pid,)
    ).fetchone()
    return dict(row) if row else dict(hide_weight=0, hide_nutrition=0, revision=0)


def normalized_answers(answers):
    answers = deepcopy(answers)
    personal = answers.get("personal", {})
    old = {k: personal.pop(k) for k in list(personal) if k in MEASUREMENT_KEYS}
    if old:
        answers["measurements"] = {**old, **answers.get("measurements", {})}
    return answers


def draft(c, pid):
    row = c.execute("SELECT * FROM intake_drafts WHERE patient_id=?", (pid,)).fetchone()
    return (
        dict(row, answers=normalized_answers(json.loads(row["answers_json"])))
        if row
        else dict(
            patient_id=pid,
            revision=0,
            catalog_version=VERSION,
            answers={},
            updated_at="",
        )
    )


def revision_check(current, form):
    if (
        str(form.get("revision", "")) != str(current["revision"])
        or form.get("catalog_version") != VERSION
    ):
        raise HTTPException(
            409,
            "Анкета уже изменилась. Обновите страницу; ваши новые ответы не перезаписаны.",
        )


def change(c, pid, user, kind, revision, payload, object_id=None):
    c.execute(
        "INSERT INTO intake_changes(patient_id,actor_id,kind,object_id,revision,payload,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            pid,
            user["id"],
            kind,
            object_id,
            revision,
            json.dumps(payload, ensure_ascii=False),
            now(),
        ),
    )
    audit(c, user["id"], "intake_" + kind, pid, object_id)


def save_draft(c, pid, user, current, answers, kind, payload, object_id=None):
    rev = current["revision"] + 1
    c.execute(
        "INSERT INTO intake_drafts VALUES(?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET revision=excluded.revision,catalog_version=excluded.catalog_version,answers_json=excluded.answers_json,updated_at=excluded.updated_at",
        (pid, rev, VERSION, json.dumps(answers, ensure_ascii=False), now()),
    )
    change(c, pid, user, kind, rev, payload, object_id)
    return rev


def save_category(c, pid, user, key, form):
    check_scope(c, user, pid, write=True)
    current = draft(c, pid)
    revision_check(current, form)
    if key == "reproductive" and not reproductive_suggested(current):
        raise HTTPException(422, "Сначала укажите женский пол в личных данных")
    fields = BY_KEY[key]["fields"]
    data = parse_answers(
        fields, form, current["answers"].get(key, {}), preferences(c, pid)
    )
    if key == "medicines":
        selected = data.get("avoid_reactions", {})
        primary = data.get("primary_reaction", {})
        if primary.get("status") == "answered" and (
            selected.get("status") != "answered"
            or primary.get("value") not in selected.get("value", [])
        ):
            raise HTTPException(
                422, "Главный приоритет должен быть одним из выбранных эффектов"
            )
    if data == current["answers"].get(key, {}) and current["revision"]:
        return current["revision"]
    answers = {**current["answers"], key: data}
    return save_draft(
        c,
        pid,
        user,
        current,
        answers,
        "answers_saved",
        dict(category=key, answers=data, catalog_version=VERSION),
    )


def entities(c, pid):
    return [
        dict(
            id=r["id"],
            patient_id=r["patient_id"],
            kind=r["kind"],
            version=r["version"],
            updated_by=r["updated_by"],
            updated_at=r["updated_at"],
            data=json.loads(r["data_json"]),
        )
        for r in c.execute(
            "SELECT * FROM intake_entities WHERE patient_id=? AND archived=0 ORDER BY id",
            (pid,),
        )
    ]


def attachments(c, pid):
    return [
        dict(r)
        for r in c.execute(
            "SELECT id,patient_id,scope,category,entity_id,filename,mime,byte_size,study_date,comment,sensitive,scan_status,scan_engine,created_at FROM clinical_attachments WHERE patient_id=? AND scope='intake' AND deleted_at IS NULL ORDER BY id",
            (pid,),
        )
    ]


def snapshot(c, pid):
    current = draft(c, pid)
    return dict(
        answers=current["answers"],
        entities=entities(c, pid),
        attachments=attachments(c, pid),
        links=[
            dict(r)
            for r in c.execute(
                "SELECT left_id,right_id FROM intake_entity_links WHERE patient_id=? ORDER BY left_id,right_id",
                (pid,),
            )
        ],
        preferences=preferences(c, pid),
        catalog_version=VERSION,
        revision=current["revision"],
    )


def entity_fields(c, pid, kind):
    fields = list(ENTITY_FIELDS[kind])
    if kind == "family":
        relatives = c.execute(
            "SELECT id,name,relation FROM relatives WHERE patient_id=? ORDER BY id",
            (pid,),
        ).fetchall()
        fields.insert(
            0,
            dict(
                key="relative_id",
                label="Связать с родственником из семейного дерева · необязательно",
                kind="choice",
                options=[str(r["id"]) for r in relatives],
                option_labels={
                    str(r["id"]): r["relation"] + " · " + r["name"] for r in relatives
                },
                extra=False,
            ),
        )
    return fields


def save_entity(c, pid, user, kind, form, eid=None):
    check_scope(c, user, pid, write=True)
    current = draft(c, pid)
    client_token = str(form.get("client_token", ""))
    if len(client_token) > 100:
        raise HTTPException(422, "Некорректный идентификатор карточки")
    if not eid and client_token:
        existing = c.execute(
            "SELECT * FROM intake_entities WHERE client_token=?", (client_token,)
        ).fetchone()
        if existing:
            if (
                existing["patient_id"] != pid
                or existing["kind"] != kind
                or existing["archived"]
            ):
                raise HTTPException(409, "Карточка недоступна")
            previous = json.loads(existing["data_json"])
            existing_links = [
                str(r["right_id"] if r["left_id"] == existing["id"] else r["left_id"])
                for r in c.execute(
                    "SELECT left_id,right_id FROM intake_entity_links WHERE patient_id=? AND (left_id=? OR right_id=?)",
                    (pid, existing["id"], existing["id"]),
                )
            ]
            if parse_answers(
                entity_fields(c, pid, kind), form, previous
            ) == previous and sorted(set(form.getlist("related"))) == sorted(
                existing_links
            ):
                return existing["id"]
            raise HTTPException(
                409, "Карточка уже сохранена. Откройте её и продолжите заполнение."
            )
    revision_check(current, form)
    old = None
    if eid:
        old = c.execute(
            "SELECT * FROM intake_entities WHERE id=? AND patient_id=? AND archived=0",
            (eid, pid),
        ).fetchone()
        if not old:
            raise HTTPException(404, "Карточка не найдена")
        if old["kind"] != kind or str(form.get("entity_version", "")) != str(
            old["version"]
        ):
            raise HTTPException(409, "Карточка изменилась")
    previous = json.loads(old["data_json"]) if old else {}
    data = parse_answers(entity_fields(c, pid, kind), form, previous)
    if not any(a["status"] != "unanswered" for a in data.values()):
        raise HTTPException(
            422, "Заполните хотя бы один ответ или отметьте тему для обсуждения"
        )
    try:
        related = list(dict.fromkeys(int(s) for s in form.getlist("related")))
    except ValueError:
        raise HTTPException(422, "Проверьте связи карточек") from None
    if len(related) > 100:
        raise HTTPException(422, "Слишком много связей")
    for target in related:
        if (
            target == eid
            or not c.execute(
                "SELECT id FROM intake_entities WHERE id=? AND patient_id=? AND archived=0",
                (target, pid),
            ).fetchone()
        ):
            raise HTTPException(422, "Связать можно только свои доступные карточки")
    if old and data == previous:
        existing_related = [
            r["right_id"] if r["left_id"] == eid else r["left_id"]
            for r in c.execute(
                "SELECT left_id,right_id FROM intake_entity_links WHERE patient_id=? AND (left_id=? OR right_id=?)",
                (pid, eid, eid),
            )
        ]
        if sorted(related) == sorted(existing_related):
            return eid
    if (
        not old
        and c.execute(
            "SELECT count(*) FROM intake_entities WHERE patient_id=?", (pid,)
        ).fetchone()[0]
        >= 200
    ):
        raise HTTPException(422, "В анкете уже 200 карточек")
    version = old["version"] + 1 if old else 1
    if old:
        c.execute(
            "UPDATE intake_entities SET version=?,data_json=?,updated_by=?,updated_at=? WHERE id=?",
            (version, json.dumps(data, ensure_ascii=False), user["id"], now(), eid),
        )
    else:
        eid = c.execute(
            "INSERT INTO intake_entities(patient_id,kind,version,data_json,updated_by,updated_at,client_token) VALUES(?,?,?,?,?,?,?)",
            (
                pid,
                kind,
                version,
                json.dumps(data, ensure_ascii=False),
                user["id"],
                now(),
                client_token,
            ),
        ).lastrowid
    c.execute(
        "DELETE FROM intake_entity_links WHERE patient_id=? AND (left_id=? OR right_id=?)",
        (pid, eid, eid),
    )
    for target in related:
        left, right = sorted([eid, target])
        c.execute(
            "INSERT INTO intake_entity_links VALUES(?,?,?,?,?)",
            (pid, left, right, user["id"], now()),
        )
    save_draft(
        c,
        pid,
        user,
        current,
        current["answers"],
        "entity_saved",
        dict(kind=kind, version=version, data=data, related=related),
        eid,
    )
    return eid


def submit(c, pid, user, form):
    check_scope(c, user, pid, write=True)
    current = draft(c, pid)
    revision_check(current, form)
    data = snapshot(c, pid)
    meaningful = any(
        a.get("status") not in (None, "unanswered")
        for key, answers in current["answers"].items()
        for field in BY_KEY[key]["fields"]
        if visible(field, answers, data["preferences"])
        for a in [answers.get(field["key"], {})]
    )
    if not current["revision"] or not (
        meaningful or data["entities"] or data["attachments"]
    ):
        raise HTTPException(
            422, "Анкета пока пустая: добавьте ответ или отметьте отложенный вопрос"
        )
    data["answers"] = {
        key: {
            field["key"]: answers[field["key"]]
            for field in BY_KEY[key]["fields"]
            if field["key"] in answers and visible(field, answers, data["preferences"])
        }
        for key, answers in data["answers"].items()
    }
    for item in data["entities"]:
        original = item["data"]
        item["data"] = {
            field["key"]: original[field["key"]]
            for field in entity_fields(c, pid, item["kind"])
            if field["key"] in original and visible(field, original)
        }
    data["catalog"] = dict(
        categories=CATEGORIES,
        entity_fields={kind: entity_fields(c, pid, kind) for kind in ENTITY_FIELDS},
        entity_labels=ENTITY_LABELS,
        entity_category=ENTITY_CATEGORY,
    )
    existing = c.execute(
        "SELECT id FROM intake_submissions WHERE patient_id=? AND revision=?",
        (pid, current["revision"]),
    ).fetchone()
    if existing:
        from .information_requests import fulfill

        fulfill(c, user, pid, explicit_submission=True)
        return existing["id"]
    ident = c.execute(
        "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (
            pid,
            current["revision"],
            VERSION,
            json.dumps(data, ensure_ascii=False),
            user["id"],
            now(),
        ),
    ).lastrowid
    change(
        c, pid, user, "submitted", current["revision"], dict(submission_id=ident), ident
    )
    from .information_requests import fulfill

    fulfill(c, user, pid, explicit_submission=True)
    return ident


def read_submission(c, pid, ident=None):
    row = c.execute(
        "SELECT * FROM intake_submissions WHERE patient_id=?"
        + (" AND id=?" if ident else "")
        + " ORDER BY id DESC LIMIT 1",
        (pid, ident) if ident else (pid,),
    ).fetchone()
    return dict(row, data=json.loads(row["payload"])) if row else None


def question_progress(fields, answers):
    shown = [
        f
        for f in fields
        if f["key"] not in ("comment", "discuss") and visible(f, answers)
    ]
    return dict(
        done=sum(answers.get(f["key"], {}).get("status") == "answered" for f in shown),
        total=len(shown),
    )


def categories_with_progress(data, prefs):
    result = []
    for category in data.get("catalog", {}).get("categories", CATEGORIES):
        answers = data.get("answers", {}).get(category["key"], {})
        applicable = [
            field
            for field in category["fields"]
            if field["key"] not in ("comment", "discuss")
            and visible(field, answers, prefs)
        ]
        done = sum(
            answers.get(f["key"], {}).get("status") == "answered" for f in applicable
        )
        deferred = sum(
            answers.get(f["key"], {}).get("status")
            not in (None, "unanswered", "answered")
            for f in applicable
        )
        records = sum(e["kind"] in category["kinds"] for e in data.get("entities", []))
        file_count = sum(
            f["category"] == category["key"] for f in data.get("attachments", [])
        )
        for entity in data.get("entities", []):
            if entity["kind"] not in category["kinds"]:
                continue
            ef = (
                data.get("catalog", {})
                .get("entity_fields", {})
                .get(entity["kind"], ENTITY_FIELDS[entity["kind"]])
            )
            shown = [
                f
                for f in ef
                if f["key"] not in ("comment", "discuss") and visible(f, entity["data"])
            ]
            done += sum(
                entity["data"].get(f["key"], {}).get("status") == "answered"
                for f in shown
            )
            applicable += shown
        result.append(
            dict(
                percent=done * 100 // len(applicable) if applicable else 0,
                category_marker=category["key"],
                **category,
                done=done,
                deferred=deferred,
                total=len(applicable),
                records=records,
                file_count=file_count,
                optional=category["key"] == "reproductive"
                and not reproductive_suggested(data),
            )
        )
    return result


def reproductive_suggested(data):
    """Current UI condition only; stored history is never removed by this rule."""
    sex = data.get("answers", {}).get("personal", {}).get("sex", {})
    return sex.get("status") == "answered" and sex.get("value") == "Женский"


def next_section(data, current):
    """The next relevant questionnaire; family stays a separate visual editor."""
    categories = [
        c["key"]
        for c in CATEGORIES
        if c["key"] != "family"
        and (c["key"] != "reproductive" or reproductive_suggested(data))
    ]
    # An optional section can also be opened directly, regardless of sex.
    if current not in categories:
        categories = [c["key"] for c in CATEGORIES if c["key"] != "family"]
    index = categories.index(current)
    return BY_KEY[categories[index + 1]] if index + 1 < len(categories) else None


def computations(data, as_of=None):
    answers = data.get("answers", {})
    personal = answers.get("personal", {})
    measurements = {
        **{k: v for k, v in personal.items() if k in MEASUREMENT_KEYS},
        **answers.get("measurements", {}),
    }

    def value(key):
        item = (measurements if key in MEASUREMENT_KEYS else personal).get(key, {})
        return item.get("value") if item.get("status") == "answered" else None

    reference = as_of or today()
    result = dict(
        age=None,
        bmi=None,
        bmi_inputs="",
        bmi_note="Укажите дату рождения в личных данных, рост в сантиметрах и массу в килограммах.",
        as_of=reference.isoformat(),
    )
    birth = date.fromisoformat(value("birth_date")) if value("birth_date") else None
    if birth and birth <= reference:
        result["age"] = (
            reference.year
            - birth.year
            - ((reference.month, reference.day) < (birth.month, birth.day))
        )
    pregnancy = answers.get("reproductive", {}).get("pregnancy", {}).get("value")
    if data.get("preferences", {}).get("hide_weight"):
        result["bmi_note"] = "Показ измерений скрыт специалистом."
        return result
    if value("bmi_restriction") not in (None, "Ограничений не знаю") or pregnancy in (
        "Беременность",
        "Возможна беременность",
    ):
        result["bmi_note"] = "Расчёт скрыт из-за указанного ограничения."
        return result
    if result["age"] is not None and result["age"] < 18:
        result["bmi_note"] = "Взрослый расчёт ИМТ не применяется до 18 лет."
        return result
    measured = (
        date.fromisoformat(value("measured_at")) if value("measured_at") else None
    )
    if (
        birth
        and measured
        and (
            measured.year
            - birth.year
            - ((measured.month, measured.day) < (birth.month, birth.day))
        )
        < 18
    ):
        result["bmi_note"] = (
            "Измерение сделано до 18 лет; взрослый расчёт не применяется."
        )
        return result
    if result["age"] is not None and value("height") and value("weight"):
        height = Decimal(str(value("height")))
        weight = Decimal(str(value("weight")))
        if height > 0 and weight > 0:
            result["bmi"] = float(
                (weight / (height / 100) ** 2).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
            )
            result["bmi_inputs"] = f"Рост: {height:g} см · масса: {weight:g} кг. "
            result["bmi_note"] = "Масса (кг) / рост (м)². " + (
                "Дата измерений: " + measured.isoformat() + "."
                if measured
                else "Дата измерений не указана."
            )
    return result


def overview(data, prefs):
    blocks = []
    deferred = []
    for category in data.get("catalog", {}).get("categories", CATEGORIES):
        answers = data.get("answers", {}).get(category["key"], {})
        values = []
        for field in category["fields"]:
            a = answers.get(field["key"], {})
            if not visible(field, answers, prefs) or a.get("status") in (
                None,
                "unanswered",
            ):
                continue
            item = dict(
                field=field,
                answer=a,
                text=display_field(field, a),
                target="answer:" + category["key"] + "." + field["key"],
            )
            if (
                a["status"] != "answered"
                or field["key"] == "discuss"
                and a.get("value") == "Да"
                or a.get("value")
                in (
                    "Да, хочу обсудить лично",
                    "Хочу обсудить лично",
                    "Предпочитаю обсудить лично",
                )
            ):
                deferred.append(dict(category=category, **item))
            elif (
                not category["sensitive"]
                and len(values) < 3
                and field["key"] not in ("comment", "discuss")
            ):
                values.append(item)
        blocks.append(
            dict(
                category=category,
                items=values,
                sensitive=category["sensitive"],
                has_data=bool(answers),
                entities=[
                    e
                    for e in data.get("entities", [])
                    if e["kind"] in category["kinds"]
                ][:3],
                file_count=sum(
                    f["category"] == category["key"]
                    for f in data.get("attachments", [])
                ),
            )
        )
    return blocks, deferred


def review(c, pid, user, form):
    check_scope(c, user, pid, doctor=True)
    try:
        sid = int(form.get("submission_id", ""))
    except ValueError:
        raise HTTPException(422, "Нужна версия анкеты") from None
    submission = read_submission(c, pid, sid)
    if not submission:
        raise HTTPException(404, "Версия анкеты не найдена")
    valid = {f'entity:{e["id"]}:{e["version"]}' for e in submission["data"]["entities"]}
    valid |= {
        "answer:" + key + "." + field
        for key, answers in submission["data"]["answers"].items()
        for field in answers
    }
    valid |= {"file:" + str(f["id"]) for f in submission["data"]["attachments"]}
    target = str(form.get("target", ""))
    status = str(form.get("status", ""))
    note = str(form.get("note", "")).strip()
    if (
        target not in valid
        or status not in ("confirmed", "clarify", "rejected", "unreadable")
        or len(note) > 2000
    ):
        raise HTTPException(422, "Проверьте запись, статус и комментарий")
    c.execute(
        "INSERT INTO intake_reviews(submission_id,target,status,note,actor_id,created_at) VALUES(?,?,?,?,?,?)",
        (sid, target, status, note, user["id"], now()),
    )
    audit(c, user["id"], "intake_fact_reviewed", pid, sid)
