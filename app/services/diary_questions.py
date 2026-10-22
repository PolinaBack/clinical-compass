"""Extend existing metrics, preserving their IDs and every old value."""

from datetime import date
import json
import re
from fastapi import HTTPException
from app.services.diary import number, metric_definition
from app.services.clinical_forms import today
from app.intake_catalog import STATES
from app.storage import now, audit

FORMATS = {
    "scale": "Шкала",
    "number": "Число",
    "boolean": "Да / нет",
    "text": "Текст",
    "choice": "Один вариант",
    "multi": "Несколько вариантов",
    "time": "Время",
    "duration": "Продолжительность, минуты",
    "date": "Дата",
    "file": "Файл / фотография",
    "voice": "Голосовая заметка",
}
MODULES = {
    "wellbeing": "Общее самочувствие",
    "emotions": "Эмоции и настроение",
    "sleep": "Сон и восстановление",
    "activity": "Активность",
    "cognitive": "Мысли и внимание",
    "medication": "Лечение и переносимость",
    "anthropometry": "Антропометрия",
    "physiology": "Физиология",
    "nutrition": "Питание и вода",
    "somatic": "Физическое самочувствие",
    "psychotherapy": "Психотерапия",
    "menstrual": "Менструальный цикл",
    "vitamins": "Витамины и БАДы",
    "goals": "Личные цели",
    "scales": "Клинические шкалы — отдельные методики",
    "materials": "Материалы",
    "consultation": "Подготовка к консультации",
    "specialized": "Другое",
}


def legacy_config(row):
    r = dict(row)
    return dict(
        title=r["title"],
        kind=r["kind"],
        unit=r["unit"],
        minimum=r["minimum"],
        maximum=r["maximum"],
        options=[],
        category="specialized",
        hint="",
        minimum_label="",
        maximum_label="",
        recall_period="Сейчас",
        frequency="daily",
        weekdays=[1, 2, 3, 4, 5, 6, 7],
        interval=1,
        start="",
        end="",
        time_window="",
        required=False,
        trigger="always",
        threshold=None,
        position=r["id"],
        advanced=False,
    )


def definitions(c, pid, all_questions=False):
    result = []
    for row in c.execute(
        "SELECT * FROM diary_metrics WHERE patient_id=? ORDER BY id", (pid,)
    ):
        m = dict(row)
        config = c.execute(
            "SELECT * FROM diary_question_config WHERE metric_id=?", (row["id"],)
        ).fetchone()
        version = config["version"] if config else 1
        saved = c.execute(
            "SELECT definition FROM diary_question_versions WHERE metric_id=? AND version=?",
            (row["id"], version),
        ).fetchone()
        definition = json.loads(saved["definition"]) if saved else legacy_config(row)
        m.update(
            definition,
            version=version,
            active=bool(config["active"]) if config else True,
        )
        if m["active"] or all_questions:
            result.append(m)
    return sorted(result, key=lambda m: (m["position"], m["id"]))


def ensure_version(c, m, user):
    exists = c.execute(
        "SELECT 1 FROM diary_question_versions WHERE metric_id=? AND version=?",
        (m["id"], m["version"]),
    ).fetchone()
    if not exists:
        definition = {
            key: value
            for key, value in m.items()
            if key
            not in ("id", "patient_id", "created_by", "created_at", "active", "version")
        }
        c.execute(
            "INSERT INTO diary_question_versions VALUES(?,?,?,?,?)",
            (
                m["id"],
                m["version"],
                json.dumps(definition, ensure_ascii=False),
                m.get("created_by", user["id"]),
                m.get("created_at", now()),
            ),
        )
        c.execute(
            "INSERT OR IGNORE INTO diary_question_config VALUES(?,?,1)",
            (m["id"], m["version"]),
        )


def configuration(form):
    if form.get("question_builder") != "yes":
        title, kind, unit, minimum, maximum = metric_definition(form)
        return legacy_config(
            dict(
                id=0,
                title=title,
                kind=kind,
                unit=unit,
                minimum=minimum,
                maximum=maximum,
            )
        )
    title = str(form.get("title", "")).strip()
    kind = str(form.get("kind", ""))
    if not 1 <= len(title) <= 100 or kind not in FORMATS:
        raise HTTPException(422, "Введите вопрос до 100 символов и выберите формат")
    category = str(form.get("category", "specialized"))
    if category not in MODULES:
        raise HTTPException(422, "Проверьте категорию")
    result = dict(title=title, kind=kind, category=category, advanced=True)
    for key, limit in [
        ("unit", 24),
        ("hint", 600),
        ("minimum_label", 100),
        ("maximum_label", 100),
        ("recall_period", 150),
        ("time_window", 100),
    ]:
        value = str(form.get(key, "")).strip()
        if len(value) > limit:
            raise HTTPException(422, "Слишком длинное описание вопроса")
        result[key] = value
    result["minimum"] = (
        float(number(form.get("minimum") or "0", "Минимум"))
        if kind == "scale"
        else (
            float(number(form["minimum"], "Минимум"))
            if form.get("minimum") and kind == "number"
            else None
        )
    )
    result["maximum"] = (
        float(number(form.get("maximum") or "10", "Максимум"))
        if kind == "scale"
        else (
            float(number(form["maximum"], "Максимум"))
            if form.get("maximum") and kind == "number"
            else None
        )
    )
    if (
        result["minimum"] is not None
        and result["maximum"] is not None
        and result["minimum"] >= result["maximum"]
    ):
        raise HTTPException(422, "Максимум должен быть больше минимума")
    if kind == "scale" and any(
        n != int(n) or abs(n) > 10000 for n in (result["minimum"], result["maximum"])
    ):
        raise HTTPException(422, "Границы шкалы должны быть целыми, от −10000 до 10000")
    if result["unit"] and kind not in ("scale", "number", "duration"):
        raise HTTPException(422, "Единица нужна только числовому ответу")
    options = [
        v.strip() for v in str(form.get("options", "")).splitlines() if v.strip()
    ]
    if kind in ("choice", "multi") and (
        not 2 <= len(options) <= 20
        or len({v.casefold() for v in options}) != len(options)
        or any(len(v) > 100 for v in options)
    ):
        raise HTTPException(
            422, "Нужны от 2 до 20 разных вариантов, по одному на строке"
        )
    result["options"] = options if kind in ("choice", "multi") else []
    freq = str(form.get("frequency", "daily"))
    if freq not in ("daily", "weekdays", "interval"):
        raise HTTPException(422, "Проверьте частоту")
    try:
        weekdays = [int(v) for v in form.getlist("weekdays")]
        interval = int(form.get("interval") or "1")
        position = int(form.get("position") or "0")
    except ValueError:
        raise HTTPException(422, "Проверьте дни и порядок вопросов") from None
    if (
        not 1 <= interval <= 365
        or not 0 <= position <= 10000
        or any(d not in range(1, 8) for d in weekdays)
        or freq == "weekdays"
        and not weekdays
    ):
        raise HTTPException(422, "Проверьте расписание")
    start = str(form.get("start", ""))
    end = str(form.get("end", ""))
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
        if end and start and end < start or freq == "interval" and not start:
            raise ValueError()
    except ValueError:
        raise HTTPException(
            422, "Для интервала нужна начальная дата; конец не может быть раньше начала"
        ) from None
    trigger = str(form.get("trigger", "always"))
    if trigger not in ("always", "wellbeing_lte", "sleep_lte", "missed"):
        raise HTTPException(422, "Проверьте условие показа")
    if trigger.endswith("_lte") and not str(form.get("threshold", "")).strip():
        raise HTTPException(422, "Укажите порог условия показа")
    threshold = (
        float(number(form.get("threshold"), "Порог"))
        if trigger.endswith("_lte")
        else None
    )
    if threshold is not None and not 0 <= threshold <= (
        24 if trigger == "sleep_lte" else 10
    ):
        raise HTTPException(422, "Порог вне диапазона базовой карточки")
    result.update(
        frequency=freq,
        weekdays=weekdays if freq == "weekdays" else list(range(1, 8)),
        interval=interval,
        position=position,
        start=start,
        end=end,
        required=form.get("required") == "yes",
        trigger=trigger,
        threshold=threshold,
    )
    return result


def save_definition(c, pid, user, form, mid=None):
    from app.services.intake import check_scope

    check_scope(c, user, pid, doctor=True)
    definition = configuration(form)
    if mid:
        old = next((m for m in definitions(c, pid, True) if m["id"] == mid), None)
        if not old:
            raise HTTPException(404, "Вопрос не найден")
        if str(form.get("revision", "")) != str(old["version"]):
            raise HTTPException(409, "Вопрос уже изменился. Обновите страницу.")
        ensure_version(c, old, user)
        version = old["version"] + 1
    else:
        count = c.execute(
            "SELECT COUNT(*) FROM diary_metrics WHERE patient_id=?", (pid,)
        ).fetchone()[0]
        if count >= 100:
            raise HTTPException(422, "В дневнике уже 100 дополнительных вопросов")
        version = 1
    storage_kind = (
        definition["kind"]
        if definition["kind"] in ("scale", "number", "boolean", "text")
        else "text"
    )
    values = (
        definition["title"],
        storage_kind,
        definition["unit"],
        definition["minimum"],
        definition["maximum"],
    )
    if not mid:
        mid = c.execute(
            "INSERT INTO diary_metrics(patient_id,title,kind,unit,minimum,maximum,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (pid, *values, user["id"], now()),
        ).lastrowid
    else:
        c.execute(
            "UPDATE diary_metrics SET title=?,kind=?,unit=?,minimum=?,maximum=? WHERE id=?",
            (*values, mid),
        )
    if not definition["position"]:
        definition["position"] = mid
    c.execute(
        "INSERT INTO diary_question_versions VALUES(?,?,?,?,?)",
        (mid, version, json.dumps(definition, ensure_ascii=False), user["id"], now()),
    )
    c.execute(
        "INSERT INTO diary_question_config VALUES(?,?,1) ON CONFLICT(metric_id) DO UPDATE SET version=excluded.version,active=1",
        (mid, version),
    )
    audit(c, user["id"], "diary_question_saved", pid, mid)
    return mid


def due(m, day, base=None):
    if (
        not m["active"]
        or m["start"]
        and day.isoformat() < m["start"]
        or m["end"]
        and day.isoformat() > m["end"]
    ):
        return False
    if m["frequency"] == "weekdays" and day.isoweekday() not in m["weekdays"]:
        return False
    if (
        m["frequency"] == "interval"
        and (day - date.fromisoformat(m["start"])).days % m["interval"]
    ):
        return False
    if base is not None:
        if (
            m["trigger"] == "wellbeing_lte"
            and float(base["wellbeing"]) > m["threshold"]
        ):
            return False
        if m["trigger"] == "sleep_lte" and float(base["sleep"]) > m["threshold"]:
            return False
        if m["trigger"] == "missed" and base["adherence"] != "missed":
            return False
    return True


def parse(c, pid, form, day, base, prepared):
    all_defs = {str(m["id"]): m for m in definitions(c, pid, True)}
    for prefix in ("metric_", "status_", "comment_", "discuss_", "version_"):
        if any(k[len(prefix) :] not in all_defs for k in form if k.startswith(prefix)):
            raise HTTPException(422, "Этот вопрос не назначен пациенту")
    result = []
    for key, m in all_defs.items():
        name = "metric_" + key
        raw = form.getlist(name)
        if any(not isinstance(v, str) for v in raw):
            raise HTTPException(422, "Файл нужно прикреплять через поле вложения")
        status = str(form.get("status_" + key, "unanswered"))
        if status not in STATES or len(form.getlist("status_" + key)) > 1:
            raise HTTPException(422, "Недопустимый статус ответа")
        if m["kind"] != "multi" and len(raw) > 1:
            raise HTTPException(422, "Ожидается один ответ")
        upload = prepared.get(int(key), [])
        if upload and m["kind"] not in ("file", "voice"):
            raise HTTPException(422, "Этот вопрос не принимает вложения")
        value = raw if m["kind"] == "multi" else str(raw[0]).strip() if raw else ""
        present = bool(value) or bool(upload)
        if present and status == "unanswered":
            status = "answered"
        if status == "answered" and not present:
            status = "unanswered"
        active = due(m, day, base)
        if not active:
            if present or status != "unanswered":
                raise HTTPException(
                    422, "Вопрос не назначен на выбранную дату или по текущему условию"
                )
            continue
        if (m["advanced"] or m["version"] > 1) and str(
            form.get("version_" + key, "")
        ) != str(m["version"]):
            raise HTTPException(
                409, "Врач изменил вопросы. Обновите дневник перед сохранением."
            )
        if m["required"] and status == "unanswered":
            raise HTTPException(
                422,
                m["title"]
                + ": нужна отметка; можно выбрать «не знаю» или «обсудить лично»",
            )
        comment = str(form.get("comment_" + key, "")).strip()
        discuss = form.get("discuss_" + key) == "yes"
        if len(comment) > 1000:
            raise HTTPException(422, "Комментарий — до 1000 символов")
        if status == "unanswered" and not comment and not discuss:
            continue
        answer = None
        display = ""
        if status == "answered":
            kind = m["kind"]
            if kind in ("number", "scale", "duration"):
                n = number(value, m["title"])
                if (
                    kind == "duration"
                    and not 0 <= n <= 10080
                    or m["minimum"] is not None
                    and n < number(m["minimum"], "Минимум")
                    or m["maximum"] is not None
                    and n > number(m["maximum"], "Максимум")
                    or kind == "scale"
                    and n != n.to_integral_value()
                ):
                    raise HTTPException(
                        422, m["title"] + ": значение вне назначенного диапазона"
                    )
                answer = display = str(n)
            elif kind == "boolean":
                if value not in ("true", "false"):
                    raise HTTPException(422, "Выберите «Да» или «Нет»")
                answer = value
                display = {"true": "Да", "false": "Нет"}[value]
            elif kind in ("choice", "multi"):
                selected = value if isinstance(value, list) else [value]
                if len(selected) != len(set(selected)) or any(
                    v not in m["options"] for v in selected
                ):
                    raise HTTPException(422, "Выберите предложенные варианты")
                answer = value
                display = ", ".join(selected)
            elif kind == "time":
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise HTTPException(422, "Введите время ЧЧ:ММ")
                answer = display = value
            elif kind == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(422, "Введите дату") from None
                answer = display = value
            elif kind in ("file", "voice"):
                if not upload:
                    raise HTTPException(422, "Выберите файл")
                if kind == "voice" and any(
                    not u[2][0].startswith("audio/") for u in upload
                ):
                    raise HTTPException(422, "Нужна аудиозаметка")
                answer = []
                display = "Вложение"
            else:
                if len(value) > 1000:
                    raise HTTPException(422, "Ответ — до 1000 символов")
                answer = display = value
        else:
            display = STATES[status]
        result.append(
            dict(
                metric=m,
                status=status,
                answer=answer,
                display=display,
                comment=comment,
                discuss=discuss,
                uploads=upload if status == "answered" else [],
            )
        )
    # Do not accept hidden/foreign uploads under another question identifier.
    if set(prepared) - {int(k) for k in all_defs}:
        raise HTTPException(422, "Файл не относится к вопросам этого пациента")
    return result


def save_answers(c, db_path, pid, user, did, items):
    from app.services import clinical_attachments as files

    for item in items:
        m = item["metric"]
        ensure_version(c, m, user)
        if item["uploads"]:
            ids = [
                files.save(
                    c,
                    db_path,
                    pid,
                    user,
                    "diary",
                    str(m["id"]),
                    None,
                    upload,
                    content,
                    {},
                    validated=validation,
                )
                for upload, content, validation in item["uploads"]
            ]
            item["answer"] = ids
            item["display"] = "Вложения: " + ", ".join("#" + str(i) for i in ids)
        # Historical text column remains available to existing consumers. Missing
        # statuses live only in metadata and can never masquerade as a numeric 0.
        if item["status"] == "answered":
            value = (
                item["answer"]
                if isinstance(item["answer"], str)
                else json.dumps(item["answer"], ensure_ascii=False)
            )
            c.execute(
                "INSERT INTO diary_metric_values VALUES(?,?,?)", (did, m["id"], value)
            )
        c.execute(
            "INSERT INTO diary_answer_details VALUES(?,?,?,?,?,?,?,?,?)",
            (
                did,
                m["id"],
                m["version"],
                item["status"],
                json.dumps(item["answer"], ensure_ascii=False),
                item["comment"],
                int(item["discuss"]),
                user["id"],
                now(),
            ),
        )
