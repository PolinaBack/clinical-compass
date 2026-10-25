"""Typed self-reports. Unknown, no, zero, skipped and declined stay distinct."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from app.intake_catalog import STATES


def today():
    return datetime.now(ZoneInfo("Europe/Moscow")).date()


def visible(field, answers, preferences=None):
    prefs = preferences or {}
    if (
        field.get("weight")
        and prefs.get("hide_weight")
        or field.get("nutrition")
        and prefs.get("hide_nutrition")
    ):
        return False
    condition = field.get("when")
    if not condition:
        return True
    parent = answers.get(condition["key"], {})
    if parent.get("status") != "answered":
        return False
    value = parent.get("value")
    values = value if isinstance(value, list) else [value]
    return any(v in condition["values"] for v in values)


def typed_value(field, raw):
    label, kind = field["label"], field["kind"]
    if kind == "multi":
        if (
            not isinstance(raw, list)
            or len(raw) != len(set(raw))
            or any(v not in field["options"] for v in raw)
            or len(raw) > field.get("max_items", len(field["options"]))
        ):
            raise HTTPException(422, f"{label}: проверьте варианты ответа")
        return raw
    if not isinstance(raw, str):
        raise HTTPException(422, f"{label}: ожидается одно значение")
    value = raw.strip()
    if len(value) > (3000 if kind == "longtext" else 500):
        raise HTTPException(422, f"{label}: слишком длинный ответ")
    if kind == "choice" and value not in field["options"]:
        raise HTTPException(422, f"{label}: выберите предложенный вариант")
    if kind in ("number", "scale"):
        try:
            n = Decimal(value.replace(",", "."))
            if (
                not n.is_finite()
                or abs(n) > Decimal("1e12")
                or (
                    field.get("minimum") is not None
                    and n < Decimal(str(field["minimum"]))
                )
                or (
                    field.get("maximum") is not None
                    and n > Decimal(str(field["maximum"]))
                )
                or (kind == "scale" and n != n.to_integral_value())
            ):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise HTTPException(422, f"{label}: недопустимое число") from None
        return int(n) if kind == "scale" else format(n, "f")
    if kind == "date":
        try:
            d = date.fromisoformat(value)
            if d.year < 1800 or field.get("past") and d > today():
                raise ValueError()
        except ValueError:
            raise HTTPException(422, f"{label}: проверьте дату") from None
        return d.isoformat()
    return value


def parse_answers(fields, form, previous, preferences=None):
    result = dict(previous)
    allowed = {f["key"] for f in fields}
    if any(k[2:] not in allowed for k in form if k.startswith(("a_", "s_"))):
        raise HTTPException(422, "Вопрос не принадлежит этому разделу")
    updates = set()
    for field in fields:
        key = field["key"]
        if "s_" + key not in form and "a_" + key not in form:
            continue
        status = str(form.get("s_" + key, "unanswered"))
        if status not in STATES or len(form.getlist("s_" + key)) > 1:
            raise HTTPException(422, "Проверьте статус ответа")
        values = form.getlist("a_" + key)
        if field["kind"] != "multi" and len(values) > 1:
            raise HTTPException(422, "Ожидается один ответ на вопрос")
        raw = values if field["kind"] == "multi" else (values[0] if values else "")
        if isinstance(raw, list):
            if any(not isinstance(v, str) for v in raw):
                raise HTTPException(422, "Варианты ответа должны быть текстом")
        elif not isinstance(raw, str):
            raise HTTPException(422, "Ожидается текстовый ответ, а не файл")
        present = bool(raw) and (isinstance(raw, list) or bool(raw.strip()))
        if status == "unanswered" and present:
            status = "answered"
        if status == "answered" and not present:
            status = "unanswered"
        result[key] = dict(
            status=status,
            value=typed_value(field, raw) if status == "answered" else None,
        )
        updates.add(key)
    for field in fields:
        key = field["key"]
        if key in updates and not visible(field, result, preferences):
            # Inactive answers remain in history and may reappear if the parent is
            # selected again; forged hidden controls cannot replace them.
            if (
                result[key] != previous.get(key, dict(status="unanswered", value=None))
                and result[key]["status"] != "unanswered"
            ):
                raise HTTPException(
                    422, "Сначала выберите соответствующую ветку вопроса"
                )
            if key in previous:
                result[key] = previous[key]
            else:
                result.pop(key, None)
    return result


def display(answer):
    if answer.get("status") != "answered":
        return STATES.get(answer.get("status"), "Пока не отвечено")
    value = answer.get("value")
    return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)


def display_field(field, answer):
    if answer.get("status") != "answered" or not field.get("option_labels"):
        return display(answer)
    labels = field["option_labels"]
    value = answer.get("value")
    values = value if isinstance(value, list) else [value]
    return ", ".join(labels.get(v, str(v)) for v in values)


def local_datetime(value):
    if not value:
        return ""
    from datetime import timezone

    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo("Europe/Moscow")).strftime(
            "%d.%m.%Y %H:%M МСК"
        )
    except ValueError:
        return str(value)
