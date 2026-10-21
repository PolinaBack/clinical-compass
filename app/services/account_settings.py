"""Persistent account preferences; none of these fields authorize processing."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from app.storage import now, audit

SECTIONS = {
    "profile": ("account_profiles", ("display_name", "timezone", "address_style")),
    "notifications": (
        "notification_preferences",
        ("diary", "medication", "tasks", "preferred_time", "quiet_enabled", "quiet_start", "quiet_end"),
    ),
    "consultations": (
        "consultation_preferences", ("audio", "video", "transcription", "summary")
    ),
}
BOOLEAN_FIELDS = {
    "diary", "medication", "tasks", "quiet_enabled",
    "audio", "video", "transcription", "summary",
}


def read_settings(c, user):
    if user["patient_id"] is not None:
        name = c.execute("SELECT name FROM patients WHERE id=?", (user["patient_id"],)).fetchone()
    else:
        name = c.execute("SELECT name FROM doctors WHERE id=?", (user["doctor_id"],)).fetchone()
    defaults = {
        "profile": dict(display_name=name[0] if name else user["username"],
                        timezone="Europe/Moscow", address_style="polite", revision=0),
        "notifications": dict(diary=0, medication=0, tasks=0, preferred_time="20:00",
                              quiet_enabled=1, quiet_start="22:00", quiet_end="08:00", revision=0),
        "consultations": dict(audio=0, video=0, transcription=0, summary=0, revision=0),
    }
    for section, (table, _) in SECTIONS.items():
        row = c.execute(f"SELECT * FROM {table} WHERE account_id=?", (user["id"],)).fetchone()
        if row:
            defaults[section].update(dict(row))
    return defaults


def save_settings(c, user, section, form):
    if section not in SECTIONS:
        raise HTTPException(404, "Раздел не найден")
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
            raise HTTPException(422, "Укажите существующий часовой пояс, например Europe/Moscow")
    if section == "notifications":
        for key in ("preferred_time", "quiet_start", "quiet_end"):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", values[key]):
                raise HTTPException(422, "Время указывается в формате ЧЧ:ММ")
        if values["quiet_enabled"] and values["quiet_start"] == values["quiet_end"]:
            raise HTTPException(422, "Начало и конец тихих часов должны различаться")
    c.execute("BEGIN IMMEDIATE")
    current = c.execute(f"SELECT revision FROM {table} WHERE account_id=?", (user["id"],)).fetchone()
    current_revision = current["revision"] if current else 0
    if current_revision != int(revision):
        raise HTTPException(409, "Настройки изменились в другой вкладке. Обновите страницу.")
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
