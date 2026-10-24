"""Published availability and atomic bookings; historical appointments stay intact."""

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from app.storage import now, audit
from app.services.intake import check_scope
from app.services.materials import doctor

UTC = timezone.utc


def utc_now():
    return datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def zone(label):
    # Earlier versions stored a free-text label rather than an IANA identifier.
    if "Москва" in label:
        return ZoneInfo("Europe/Moscow")
    match = re.fullmatch(r"UTC([+-])(\d{1,2})(?::(\d{2}))?", label)
    if match:
        delta = timedelta(hours=int(match[2]), minutes=int(match[3] or 0))
        try:
            return timezone(delta if match[1] == "+" else -delta)
        except ValueError:
            pass
    try:
        return ZoneInfo(label)
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(
            422, "Укажите часовой пояс, например Europe/Moscow"
        ) from None


def local_start(value, label):
    try:
        naive = datetime.fromisoformat(value)
        if naive.tzinfo is not None:
            raise ValueError()
        tz = zone(label)
        aware = naive.replace(tzinfo=tz)
        if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
            raise ValueError()
        if (
            naive.replace(tzinfo=tz, fold=0).utcoffset()
            != naive.replace(tzinfo=tz, fold=1).utcoffset()
        ):
            raise ValueError()
        return aware
    except ValueError:
        raise HTTPException(
            422,
            "Проверьте дату и время: переход часового пояса не должен быть неоднозначным",
        ) from None


def duration(raw):
    try:
        value = int(raw)
        if not 5 <= value <= 240:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, "Длительность — от 5 до 240 минут") from None


def connection_details(url, fallback):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
        if (
            not valid
            or any(ch.isspace() or ord(ch) < 32 for ch in url)
            or len(url) > 2000
        ):
            raise ValueError()
    except ValueError:
        raise HTTPException(
            422, "Нужна HTTPS-ссылка сервиса видеосвязи без пароля и пробелов"
        ) from None
    if not 1 <= len(fallback.strip()) <= 1000:
        raise HTTPException(422, "Укажите способ связи при обрыве, до 1000 символов")
    return url, fallback.strip()


def event(c, user, sid, kind, aid=None):
    c.execute(
        "INSERT INTO consultation_slot_events(slot_id,actor_id,kind,appointment_id,created_at) VALUES(?,?,?,?,?)",
        (sid, user["id"], kind, aid, now()),
    )


def conflict(c, did, start, end, exclude_appointment=None):
    if c.execute(
        "SELECT 1 FROM consultation_slots WHERE doctor_id=? AND status IN ('open','booked') AND starts_at<? AND ends_at>?",
        (did, stamp(end), stamp(start)),
    ).fetchone():
        raise HTTPException(409, "Это время пересекается с уже опубликованным окном")
    for visit in c.execute(
        "SELECT a.* FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE p.doctor_id=? AND a.slot_id IS NULL AND a.status IN ('planned','ready')",
        (did,),
    ):
        if visit["id"] == exclude_appointment:
            continue
        try:
            before = local_start(visit["starts_at"], visit["timezone_label"])
        except HTTPException:
            # No guessed timezone or duration for historical appointments.
            if visit["starts_at"][:10] in {
                start.date().isoformat(),
                end.date().isoformat(),
            }:
                raise HTTPException(
                    409, "Сначала уточните время прежнего приёма в календаре"
                ) from None
            continue
        if not visit["ends_at"]:
            if before.astimezone(start.tzinfo).date() in {start.date(), end.date()}:
                raise HTTPException(
                    409, "Сначала укажите длительность прежнего приёма в календаре"
                )
        elif before.astimezone(UTC) < end.astimezone(UTC) and datetime.fromisoformat(
            visit["ends_at"]
        ) > start.astimezone(UTC):
            raise HTTPException(409, "Это время занято прежним приёмом")


def publish(c, user, form, *, require_connection=False):
    doctor(c, user)
    label = str(form.get("timezone", "Europe/Moscow"))
    # New slots always use a real IANA zone, not a guessed text label.
    try:
        ZoneInfo(label)
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, "Выберите действительный часовой пояс IANA") from None
    start = local_start(str(form.get("starts_at", "")), label)
    end = (
        start.astimezone(UTC)
        + timedelta(minutes=duration(form.get("duration_minutes")))
    ).astimezone(start.tzinfo)
    if start <= utc_now():
        raise HTTPException(422, "Опубликовать можно только будущее время")
    url, fallback = (
        str(form.get("url", "")).strip(),
        str(form.get("fallback", "")).strip(),
    )
    if require_connection or url:
        url, fallback = connection_details(url, fallback)
    elif len(fallback) > 1000:
        raise HTTPException(422, "Резервный контакт — до 1000 символов")
    conflict(c, user["doctor_id"], start, end)
    sid = c.execute(
        "INSERT INTO consultation_slots(doctor_id,created_by,starts_at,ends_at,timezone,url,fallback,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            user["doctor_id"],
            user["id"],
            stamp(start),
            stamp(end),
            label,
            url,
            fallback,
            now(),
            now(),
        ),
    ).lastrowid
    event(c, user, sid, "published")
    audit(c, user["id"], "consultation_slot_published", None, sid)
    return sid


def book(c, user, pid, sid, *, connection=None):
    check_scope(c, user, pid, write=user["role"] == "patient")
    patient = c.execute("SELECT doctor_id FROM patients WHERE id=?", (pid,)).fetchone()
    slot = c.execute(
        "SELECT * FROM consultation_slots WHERE id=? AND doctor_id=?",
        (sid, patient["doctor_id"]),
    ).fetchone()
    if not slot:
        raise HTTPException(404, "Доступное время не найдено")
    if slot["status"] == "booked":
        old = c.execute(
            "SELECT * FROM appointments WHERE id=?", (slot["appointment_id"],)
        ).fetchone()
        if old and old["patient_id"] == pid and old["status"] in ("planned", "ready"):
            return old["id"]
    if (
        slot["status"] != "open"
        or datetime.fromisoformat(slot["starts_at"]) <= utc_now()
    ):
        raise HTTPException(
            409, "Это время уже занято или недоступно. Выберите другое окно"
        )
    if connection is not None:
        doctor(c, user)
        url, fallback = connection_details(*connection)
    elif slot["url"]:
        url, fallback = connection_details(slot["url"], slot["fallback"])
    else:
        raise HTTPException(
            422 if user["role"] == "doctor" else 409,
            "Для назначения встречи врач должен добавить ссылку на видеоприём и резервный контакт.",
        )
    local = datetime.fromisoformat(slot["starts_at"]).astimezone(zone(slot["timezone"]))
    aid = c.execute(
        "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at,slot_id,ends_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            pid,
            slot["created_by"],
            local.replace(tzinfo=None).isoformat(timespec="minutes"),
            slot["timezone"],
            url,
            fallback,
            now(),
            sid,
            slot["ends_at"],
        ),
    ).lastrowid
    c.execute(
        "UPDATE consultation_slots SET status='booked',appointment_id=?,url=?,fallback=?,updated_at=? WHERE id=?",
        (aid, url, fallback, now(), sid),
    )
    c.execute(
        "INSERT INTO consultation_requests(patient_id,preferred_day,time_window,timezone_label,status,appointment_id,created_at) VALUES(?,?,?,?,'confirmed',?,?)",
        (
            pid,
            local.date().isoformat(),
            local.strftime("%H:%M"),
            slot["timezone"],
            aid,
            now(),
        ),
    )
    event(c, user, sid, "booked", aid)
    audit(c, user["id"], "consultation_booked", pid, aid)
    return aid


def release(c, user, visit):
    if not visit["slot_id"]:
        return
    slot = c.execute(
        "SELECT * FROM consultation_slots WHERE id=?", (visit["slot_id"],)
    ).fetchone()
    if slot and slot["appointment_id"] == visit["id"]:
        status = (
            "open"
            if datetime.fromisoformat(slot["starts_at"]) > utc_now()
            else "cancelled"
        )
        c.execute(
            "UPDATE consultation_slots SET status=?,appointment_id=NULL,updated_at=? WHERE id=?",
            (status, now(), slot["id"]),
        )
        event(c, user, slot["id"], "booking_cancelled", visit["id"])


def available(c, did):
    result = []
    for r in c.execute(
        "SELECT * FROM consultation_slots WHERE doctor_id=? AND status='open' AND starts_at>? ORDER BY starts_at",
        (did, stamp(utc_now())),
    ):
        start = datetime.fromisoformat(r["starts_at"]).astimezone(zone(r["timezone"]))
        end = datetime.fromisoformat(r["ends_at"]).astimezone(zone(r["timezone"]))
        result.append(
            dict(r, label=f"{start:%d.%m.%Y · %H:%M}–{end:%H:%M} · {r['timezone']}")
        )
    return result


def calendar_context(c, user):
    return dict(
        available_slots=available(c, user["doctor_id"]),
        slot_timezone=(
            c.execute(
                "SELECT timezone FROM account_profiles WHERE account_id=?",
                (user["id"],),
            ).fetchone()
            or {"timezone": "Europe/Moscow"}
        )["timezone"],
        legacy_visits=[
            dict(r)
            for r in c.execute(
                "SELECT a.*,p.name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE p.doctor_id=? AND a.status IN ('planned','ready') AND a.ends_at IS NULL ORDER BY a.starts_at",
                (user["doctor_id"],),
            )
        ],
    )


def profile_context(c, user, pid):
    from app.services import meetings
    from app.services.consent import context_access

    did = c.execute("SELECT doctor_id FROM patients WHERE id=?", (pid,)).fetchone()[
        "doctor_id"
    ]
    visits = [
        dict(r)
        for r in c.execute(
            "SELECT a.*,m.id meeting_id,m.context_id FROM appointments a LEFT JOIN meeting_sessions m ON m.appointment_id=a.id WHERE a.patient_id=? ORDER BY a.starts_at DESC,a.id DESC",
            (pid,),
        )
    ]
    briefs = []
    for visit in visits:
        if not visit["meeting_id"]:
            continue
        try:
            context_access(c, visit["context_id"], user["id"])
        except HTTPException:
            visit["meeting_id"] = None
            continue
        for record in c.execute(
            "SELECT id FROM meeting_recordings WHERE meeting_id=? AND deleted_at IS NULL AND expires_at>? ORDER BY id DESC",
            (visit["meeting_id"], now()),
        ):
            published = meetings.published(c, record["id"])
            summary = (
                meetings.latest(c, record["id"], "summary")
                if user["role"] == "doctor"
                else published
            )
            if summary:
                data = json.loads(summary["payload"])
                briefs.append(
                    dict(
                        visit=visit,
                        record_id=record["id"],
                        confirmed=bool(published and published["id"] == summary["id"]),
                        summary=[i["text"] for i in data["summary"] if i["included"]],
                        next_steps=[
                            i["text"] for i in data["next_steps"] if i["included"]
                        ],
                    )
                )
    return dict(
        # Unlinked hours remain in the doctor's calendar. A patient can only
        # self-book an invitation whose connection details are ready.
        available_slots=[s for s in available(c, did) if s["url"]],
        appointments=visits,
        latest_brief=briefs[0] if briefs else None,
        meeting_history=briefs[1:],
    )
