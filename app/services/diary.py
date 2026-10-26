"""Additive diary measurements. Definitions belong to the leading doctor."""

from decimal import Decimal, InvalidOperation
from fastapi import HTTPException


def number(value, label):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{label}: введите число")
    if not result.is_finite() or abs(result) > Decimal("1e12"):
        raise HTTPException(422, f"{label}: недопустимое число")
    return result


def sleep_rating(raw):
    if raw is None or not str(raw).strip():
        return None
    value = number(raw, "Качество сна")
    if value != value.to_integral_value() or not 0 <= value <= 10:
        raise HTTPException(422, "Качество сна: выберите целое число от 0 до 10")
    return int(value)


def metric_definition(form):
    title = str(form.get("title", "")).strip()
    unit = str(form.get("unit", "")).strip()
    kind = str(form.get("kind", ""))
    if (
        not 1 <= len(title) <= 100
        or len(unit) > 24
        or kind not in ("scale", "number", "boolean", "text")
    ):
        raise HTTPException(422, "Проверьте название, формат и единицу показателя")
    minimum = maximum = None
    if kind == "scale":
        minimum, maximum = 0, 10
    elif kind == "number":
        minimum = number(form["minimum"], "Минимум") if form.get("minimum") else None
        maximum = number(form["maximum"], "Максимум") if form.get("maximum") else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise HTTPException(422, "Минимум не может быть больше максимума")
    elif unit:
        raise HTTPException(422, "Единица нужна только для числового показателя")
    return (
        title,
        kind,
        unit,
        float(minimum) if minimum is not None else None,
        float(maximum) if maximum is not None else None,
    )


def read_diary_extras(c, pid):
    import json
    from app.services.diary_questions import definitions
    from app.intake_catalog import STATES

    all_metrics = definitions(c, pid, True)
    metrics = [m for m in all_metrics if m["active"]]
    by_id = {m["id"]: m for m in all_metrics}
    details = {
        (r["diary_id"], r["metric_id"]): dict(r)
        for r in c.execute(
            "SELECT x.* FROM diary_answer_details x JOIN diary d ON d.id=x.diary_id WHERE d.patient_id=?",
            (pid,),
        )
    }
    entries = {
        r["id"]: dict(r)
        for r in c.execute(
            "SELECT id,day FROM diary WHERE patient_id=? ORDER BY day,id", (pid,)
        )
    }
    values = {
        (r["diary_id"], r["metric_id"]): dict(r)
        for r in c.execute(
            "SELECT v.* FROM diary_metric_values v JOIN diary d ON d.id=v.diary_id WHERE d.patient_id=?",
            (pid,),
        )
    }
    history, latest = {}, {}
    for did, mid in sorted(
        set(values) | set(details), key=lambda pair: (entries[pair[0]]["day"], pair)
    ):
        metric = by_id.get(mid)
        if not metric:
            continue
        detail = details.get((did, mid))
        version = detail["version"] if detail else 1
        saved = c.execute(
            "SELECT definition FROM diary_question_versions WHERE metric_id=? AND version=?",
            (mid, version),
        ).fetchone()
        definition = json.loads(saved["definition"]) if saved else metric
        raw = values.get((did, mid), {}).get("value", "")
        if detail:
            answer = json.loads(detail["answer_json"])
            status = detail["status"]
            text = (
                STATES[status]
                if status != "answered"
                else (
                    ", ".join(str(v) for v in answer)
                    if isinstance(answer, list)
                    else (
                        {"true": "Да", "false": "Нет"}.get(str(answer), str(answer))
                        if definition["kind"] == "boolean"
                        else str(answer)
                    )
                )
            )
            if (
                status == "answered"
                and definition["kind"] == "effects"
                and isinstance(answer, dict)
            ):
                text = (
                    ", ".join(answer.get("effects", []))
                    + ". Основное: "
                    + answer.get("primary", "")
                )
            attachment_ids = (
                answer
                if definition["kind"] in ("file", "voice") and isinstance(answer, list)
                else []
            )
            if attachment_ids:
                text = "Вложения: " + ", ".join("#" + str(v) for v in attachment_ids)
        else:
            status = "answered"
            text = (
                {"true": "Да", "false": "Нет"}.get(raw, raw)
                if definition["kind"] == "boolean"
                else raw
            )
            attachment_ids = []
        item = dict(
            diary_id=did,
            metric_id=mid,
            day=entries[did]["day"],
            title=definition["title"],
            kind=definition["kind"],
            unit=definition.get("unit", ""),
            display=text,
            value=raw,
            status=status,
            version=version,
            comment=detail["comment"] if detail else "",
            discuss=bool(detail["discuss"]) if detail else False,
            attachment_ids=attachment_ids,
            effect_note=(
                answer.get("note", "")
                if detail
                and isinstance(answer, dict)
                and definition["kind"] == "effects"
                else ""
            ),
            medicine_context=(
                answer.get("medicine_context", [])
                if detail
                and isinstance(answer, dict)
                and definition["kind"] == "effects"
                else []
            ),
        )
        history.setdefault(did, []).append(item)
        latest[mid] = item
    return dict(
        diary_metrics=metrics,
        diary_metrics_all=all_metrics,
        diary_metric_history=history,
        diary_metric_latest=latest,
    )


def parse_metric_values(c, pid, form):
    definitions = {
        str(r["id"]): dict(r)
        for r in c.execute("SELECT * FROM diary_metrics WHERE patient_id=?", (pid,))
    }
    values = []
    for key in form:
        if not key.startswith("metric_"):
            continue
        metric = definitions.get(key[7:])
        if not metric or len(form.getlist(key)) != 1:
            raise HTTPException(422, "Этот показатель не назначен данному пациенту")
        value = str(form[key]).strip()
        if not value:
            continue
        if metric["kind"] in ("scale", "number"):
            result = number(value, metric["title"])
            if (
                metric["minimum"] is not None
                and result < Decimal(str(metric["minimum"]))
            ) or (
                metric["maximum"] is not None
                and result > Decimal(str(metric["maximum"]))
            ):
                raise HTTPException(
                    422, f"{metric['title']}: значение вне назначенного диапазона"
                )
            if metric["kind"] == "scale" and result != result.to_integral_value():
                raise HTTPException(422, "Для шкалы 0–10 выберите целое число")
            value = str(result)
        elif metric["kind"] == "boolean" and value not in ("true", "false"):
            raise HTTPException(422, "Выберите «Да» или «Нет»")
        elif metric["kind"] == "text" and len(value) > 1000:
            raise HTTPException(422, "Ответ не должен быть длиннее 1000 символов")
        values.append((metric["id"], value))
    return values
