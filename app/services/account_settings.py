"""Persistent preferences and explicit patient permissions with versioned history."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from app.storage import now, audit
from app.services.consent import (
    MEDIA_FIELDS,
    account_context,
    latest_event,
    record_consent,
)

SECTIONS = {
    "profile": ("account_profiles", ("display_name", "timezone", "address_style")),
    "notifications": (
        "notification_preferences",
        (
            "diary",
            "medication",
            "tasks",
            "preferred_time",
            "quiet_enabled",
            "quiet_start",
            "quiet_end",
        ),
    ),
    "consultations": (
        "consultation_preferences",
        ("audio", "video", "transcription", "summary"),
    ),
}
BOOLEAN_FIELDS = {
    "diary",
    "medication",
    "tasks",
    "quiet_enabled",
    "audio",
    "video",
    "transcription",
    "summary",
}


def read_settings(c, user):
    if user["patient_id"] is not None:
        name = c.execute(
            "SELECT name FROM patients WHERE id=?", (user["patient_id"],)
        ).fetchone()
    else:
        name = c.execute(
            "SELECT name FROM doctors WHERE id=?", (user["doctor_id"],)
        ).fetchone()
    defaults = {
        "profile": dict(
            display_name=name[0] if name else user["username"],
            timezone="Europe/Moscow",
            address_style="polite",
            revision=0,
        ),
        "notifications": dict(
            diary=0,
            medication=0,
            tasks=0,
            preferred_time="20:00",
            quiet_enabled=1,
            quiet_start="22:00",
            quiet_end="08:00",
            revision=0,
        ),
        "consultations": dict(audio=0, video=0, transcription=0, summary=0, revision=0),
    }
    for section, (table, _) in SECTIONS.items():
        row = c.execute(
            f"SELECT * FROM {table} WHERE account_id=?", (user["id"],)
        ).fetchone()
        if row:
            defaults[section].update(dict(row))
    return defaults


def save_settings(c, user, section, form):
    if section not in SECTIONS:
        raise HTTPException(404, "Раздел не найден")
    if section == "consultations" and user["role"] != "patient":
        raise HTTPException(
            403, "Разрешения встреч находятся только в настройках пациента"
        )
    table, fields = SECTIONS[section]
    revision = str(form.get("revision", ""))
    if not revision.isdigit():
        raise HTTPException(422, "Обновите страницу настроек")
    values = {}
    for key in fields:
        value = str(form.get(key, "")).strip()
        if key in BOOLEAN_FIELDS:
            if value not in ("", "true"):
                raise HTTPException(422, "Недопустимое значение переключателя")
            values[key] = int(value == "true")
        else:
            values[key] = value
    if section == "profile":
        if not 1 <= len(values["display_name"]) <= 80:
            raise HTTPException(422, "Имя должно содержать от 1 до 80 символов")
        if values["address_style"] not in ("polite", "friendly"):
            raise HTTPException(422, "Выберите обращение «вы» или «ты»")
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(
                422, "Укажите существующий часовой пояс, например Europe/Moscow"
            )
    if section == "notifications":
        for key in ("preferred_time", "quiet_start", "quiet_end"):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", values[key]):
                raise HTTPException(422, "Время указывается в формате ЧЧ:ММ")
        if values["quiet_enabled"] and values["quiet_start"] == values["quiet_end"]:
            raise HTTPException(422, "Начало и конец тихих часов должны различаться")
    c.execute("BEGIN IMMEDIATE")
    current = c.execute(
        f"SELECT revision FROM {table} WHERE account_id=?", (user["id"],)
    ).fetchone()
    current_revision = current["revision"] if current else 0
    if current_revision != int(revision):
        raise HTTPException(
            409, "Настройки изменились в другой вкладке. Обновите страницу."
        )
    columns = ",".join(fields)
    updates = ",".join(f"{key}=excluded.{key}" for key in fields)
    placeholders = ",".join("?" for _ in fields)
    c.execute(
        f"""INSERT INTO {table}(account_id,{columns},revision,updated_at)
            VALUES(?,{placeholders},?,?)
            ON CONFLICT(account_id) DO UPDATE SET {updates},
                revision=excluded.revision,updated_at=excluded.updated_at""",
        (user["id"], *(values[k] for k in fields), current_revision + 1, now()),
    )
    audit(c, user["id"], "settings_" + section + "_saved", user["patient_id"])
    if section == "consultations":
        context = account_context(c, user["id"], create=True)
        for purpose, key in MEDIA_FIELDS.items():
            try:
                document_id = int(form[f"document_id_{purpose}"])
                expected_id = int(form[f"expected_id_{purpose}"])
            except (KeyError, ValueError, TypeError):
                raise HTTPException(422, "Обновите страницу разрешений встреч")
            previous = latest_event(c, user["id"], purpose, context)
            action = (
                "granted"
                if values[key]
                else (
                    "withdrawn"
                    if previous and previous["status"] == "granted"
                    else "declined"
                )
            )
            record_consent(
                c,
                user,
                purpose,
                context,
                action,
                document_id,
                expected_id,
                bool(values[key]),
            )
    elif (
        section == "notifications"
        and user["role"] == "patient"
        and "consent_document_id" in form
    ):
        try:
            document_id = int(form["consent_document_id"])
            expected_id = int(form["consent_expected_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "Обновите страницу настроек уведомлений")
        previous = latest_event(c, user["id"], "notifications")
        enabled = form.get("enabled") == "true"
        action = (
            "granted"
            if enabled
            else (
                "withdrawn"
                if previous and previous["status"] == "granted"
                else "declined"
            )
        )
        record_consent(
            c, user, "notifications", None, action, document_id, expected_id, enabled
        )
