"""Timezone-aware week/day views over existing availability and appointments."""

from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from app.services import consultations as slots
from app.services.intake import check_scope
from app.storage import now, audit

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def location(pid=None, day=None, mid=None, aid=None):
    values = {
        k: v
        for k, v in dict(
            patient_id=pid, week=day, meeting_id=mid, appointment_id=aid
        ).items()
        if v is not None
    }
    return (
        "/doctor/calendar"
        + ("?" + urlencode(values) if values else "")
        + "#meeting-workspace"
    )


def context(
    c, user, *, week=None, view="week", pid=None, aid=None, mid=None, timezone=None
):
    slots.doctor(c, user)
    availability = slots.calendar_context(c, user)
    label = timezone or availability["slot_timezone"]
    try:
        tz = ZoneInfo(label)
    except (ValueError, ZoneInfoNotFoundError):
        if timezone:
            raise HTTPException(422, "Выберите действительный часовой пояс.") from None
        label, tz = "Europe/Moscow", ZoneInfo("Europe/Moscow")
    today = slots.utc_now().astimezone(tz).date()
    try:
        chosen = date.fromisoformat(week) if week else today
        if not 1900 <= chosen.year <= 2100:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Проверьте дату календаря.") from None
    if view not in ("week", "day"):
        raise HTTPException(422, "Выберите день или неделю.")
    start = chosen if view == "day" else chosen - timedelta(days=chosen.weekday())
    days = [start + timedelta(days=i) for i in range(1 if view == "day" else 7)]
    patients = [
        dict(r)
        for r in c.execute(
            "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name,id",
            (user["doctor_id"],),
        )
    ]
    if mid:
        row = c.execute(
            "SELECT m.patient_id,m.appointment_id FROM meeting_sessions m JOIN patients p ON p.id=m.patient_id WHERE m.id=? AND p.doctor_id=?",
            (mid, user["doctor_id"]),
        ).fetchone()
        if not row or pid and row["patient_id"] != pid:
            raise HTTPException(404, "Встреча не найдена для выбранного пациента.")
        pid, aid = row["patient_id"], row["appointment_id"]
    if pid is not None:
        check_scope(c, user, pid, doctor=True)
    elif patients:
        pid = patients[0]["id"]
    selected = next((p for p in patients if p["id"] == pid), None)
    visits = []
    brief = None
    if pid:
        patient_context = slots.profile_context(c, user, pid)
        visits = patient_context["appointments"]
        brief = patient_context["latest_brief"]
    if aid is not None and not any(v["id"] == aid for v in visits):
        raise HTTPException(404, "Приём не найден для выбранного пациента.")
    default_visit = (
        brief["visit"]
        if brief
        else next((v for v in visits if v["meeting_id"]), visits[0] if visits else None)
    )
    selected_visit = next((v for v in visits if v["id"] == aid), default_visit)
    if selected_visit:
        mid = selected_visit["meeting_id"]
    previous_connection = c.execute(
        "SELECT a.url,a.fallback FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE p.doctor_id=? AND a.url!='' ORDER BY a.id DESC LIMIT 1",
        (user["doctor_id"],),
    ).fetchone()
    if previous_connection is None and availability["available_slots"]:
        previous_connection = availability["available_slots"][-1]
    events, unscheduled = [], []
    for row in c.execute(
        "SELECT a.*,p.name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE p.doctor_id=? AND a.status!='cancelled'",
        (user["doctor_id"],),
    ):
        try:
            begins = slots.local_start(
                row["starts_at"], row["timezone_label"]
            ).astimezone(tz)
            ends = (
                datetime.fromisoformat(row["ends_at"]).astimezone(tz)
                if row["ends_at"]
                else begins + timedelta(minutes=30)
            )
            if ends <= begins:
                raise ValueError()
        except (HTTPException, ValueError):
            unscheduled.append(dict(row))
            continue
        events.append(
            dict(
                id="visit-" + str(row["id"]),
                name=row["name"],
                kind="visit",
                status=row["status"],
                start=begins,
                end=ends,
                unknown_duration=not row["ends_at"],
                href=location(
                    row["patient_id"], begins.date().isoformat(), aid=row["id"]
                ),
            )
        )
    for row in availability["available_slots"]:
        begins = datetime.fromisoformat(row["starts_at"]).astimezone(tz)
        ends = datetime.fromisoformat(row["ends_at"]).astimezone(tz)
        events.append(
            dict(
                id="slot-" + str(row["id"]),
                slot_id=row["id"],
                name="Свободное окно",
                kind="open",
                status="open",
                start=begins,
                end=ends,
                unknown_duration=False,
            )
        )
    calendar_days = []
    for day in days:
        pieces = []
        for event in events:
            beginning = datetime.combine(day, datetime.min.time(), tz)
            ending = beginning + timedelta(days=1)
            if event["start"] >= ending or event["end"] <= beginning:
                continue
            clip_start, clip_end = max(event["start"], beginning), min(
                event["end"], ending
            )
            top = clip_start.hour * 60 + clip_start.minute
            end_minutes = (
                1440 if clip_end.date() != day else clip_end.hour * 60 + clip_end.minute
            )
            pieces.append(
                dict(
                    event,
                    top=top,
                    height=max(24, end_minutes - top),
                    time=f"{event['start']:%H:%M}–{event['end']:%H:%M}",
                )
            )
        # Separate old overlapping appointments rather than placing text on top of text.
        lanes = []
        for piece in sorted(pieces, key=lambda e: (e["top"], e["height"])):
            lane = next(
                (i for i, end in enumerate(lanes) if end <= piece["top"]), len(lanes)
            )
            if lane == len(lanes):
                lanes.append(0)
            lanes[lane] = piece["top"] + piece["height"]
            piece["lane"] = lane
        for piece in pieces:
            piece["lanes"] = max(1, len(lanes))
        calendar_days.append(
            dict(
                date=day.isoformat(),
                label=f"{WEEKDAYS[day.weekday()]} {day:%d.%m}",
                today=day == today,
                events=pieces,
            )
        )
    query = dict(view=view, timezone=label)
    if pid:
        query["patient_id"] = pid
    step = timedelta(days=len(days))
    raw_offset = slots.utc_now().astimezone(tz).strftime("%z")
    zone_title = {"Europe/Moscow": "Москва", "UTC": "UTC"}.get(label, label)
    zone_title += " · UTC" + raw_offset[:3] + ":" + raw_offset[3:]
    return dict(
        **availability,
        calendar_default_url=previous_connection["url"] if previous_connection else "",
        calendar_default_fallback=(
            previous_connection["fallback"] if previous_connection else ""
        ),
        calendar_days=calendar_days,
        calendar_times=[f"{i//60:02d}:{i%60:02d}" for i in range(0, 1440, 30)],
        picker_times=[f"{i//60:02d}:{i%60:02d}" for i in range(0, 1440, 5)],
        calendar_zone=label,
        calendar_zone_title=zone_title,
        calendar_date=chosen.isoformat(),
        calendar_today=today.isoformat(),
        calendar_view=view,
        calendar_range=f"{days[0]:%d.%m.%Y} — {days[-1]:%d.%m.%Y}",
        calendar_previous="/doctor/calendar?"
        + urlencode(query | dict(week=(chosen - step).isoformat())),
        calendar_next="/doctor/calendar?"
        + urlencode(query | dict(week=(chosen + step).isoformat())),
        calendar_now="/doctor/calendar?" + urlencode(query),
        calendar_patients=patients,
        selected_patient=selected,
        selected_visit=selected_visit,
        calendar_visits=visits,
        selected_meeting_id=mid,
        unscheduled_visits=unscheduled,
    )


def create(c, user, form):
    slots.doctor(c, user)
    label = str(form.get("timezone", "Europe/Moscow"))
    if not 1 <= len(label) <= 80:
        raise HTTPException(422, "Выберите действительный часовой пояс.")
    try:
        pid = int(form["patient_id"]) if form.get("patient_id") else None
    except (ValueError, KeyError):
        raise HTTPException(422, "Выберите пациента из списка.") from None
    if pid is not None and not 0 < pid < 2**63:
        raise HTTPException(422, "Выберите пациента из списка.")
    if pid is not None:
        check_scope(c, user, pid, doctor=True)
    value = str(form.get("date", "")) + "T" + str(form.get("time", ""))
    begins = slots.local_start(value, label)
    minutes = slots.duration(form.get("duration_minutes"))
    if begins > slots.utc_now():
        data = dict(form, starts_at=value, duration_minutes=minutes)
        sid = slots.publish(c, user, data)
        aid = slots.book(c, user, pid, sid) if pid else None
        return dict(
            slot_id=sid,
            appointment_id=aid,
            patient_id=pid,
            day=begins.date().isoformat(),
        )
    if not pid:
        raise HTTPException(
            422,
            "Свободное окно можно открыть только на будущее время. Для записи прошедшей встречи выберите пациента.",
        )
    ends = begins.astimezone(slots.UTC) + timedelta(minutes=minutes)
    aid = c.execute(
        "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,status,created_at,ends_at) VALUES(?,?,?,?,?,?,'completed',?,?)",
        (
            pid,
            user["id"],
            begins.replace(tzinfo=None).isoformat(timespec="minutes"),
            label,
            "",
            "",
            now(),
            slots.stamp(ends),
        ),
    ).lastrowid
    audit(c, user["id"], "historical_meeting_added", pid, aid)
    return dict(appointment_id=aid, patient_id=pid, day=begins.date().isoformat())
