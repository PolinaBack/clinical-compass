"""Availability management in the existing doctor calendar."""

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.services import consultations as service
from app.storage import now, audit
import hashlib
import json
from urllib.parse import urlencode


def register_consultations(app, db, account, access, csrf_check):
    @app.post("/doctor/calendar/events")
    async def create_event(request: Request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Календарь изменяет врач.")
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        allowed = {
            "csrf",
            "calendar_token",
            "date",
            "time",
            "duration_minutes",
            "end_time",
            "end_date",
            "ends_next_day",
            "timezone",
            "patient_id",
            "url",
            "fallback",
            "event_kind",
            "return_to",
        }
        if any(k not in allowed or len(form.getlist(k)) != 1 for k in form):
            raise HTTPException(422, "Проверьте поля записи.")
        nonce = str(form.get("calendar_token", ""))
        if nonce not in request.session.get("calendar_tokens", []):
            raise HTTPException(409, "Обновите календарь перед сохранением.")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    k: str(v)
                    for k, v in form.items()
                    if k not in ("csrf", "calendar_token", "return_to")
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        from app.services.doctor_calendar import create

        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT * FROM save_requests WHERE token=?", (nonce,)
            ).fetchone()
            if previous:
                saved = json.loads(previous["payload"])
                if (
                    previous["account_id"] != user["id"]
                    or saved.get("hash") != fingerprint
                ):
                    raise HTTPException(
                        409, "Форма уже отправлена. Обновите календарь."
                    )
                result = saved["calendar"]
            else:
                result = create(c, user, form)
                c.execute(
                    "INSERT INTO save_requests(token,account_id,payload) VALUES(?,?,?)",
                    (
                        nonce,
                        user["id"],
                        json.dumps(dict(hash=fingerprint, calendar=result)),
                    ),
                )
        query = dict(
            week=result["day"], timezone=str(form.get("timezone", "Europe/Moscow"))
        )
        if result.get("patient_id"):
            query["patient_id"] = result["patient_id"]
        if result.get("appointment_id"):
            query["appointment_id"] = result["appointment_id"]
        return RedirectResponse(
            "/doctor/calendar?"
            + urlencode(query)
            + (
                "#meeting-workspace"
                if result.get("appointment_id")
                else "#availability"
            ),
            status_code=303,
        )

    @app.post("/doctor/calendar/slots")
    async def publish(request: Request):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.publish(c, user, form)
        return RedirectResponse("/doctor/calendar#availability", status_code=303)

    @app.post("/doctor/calendar/slots/{sid}/cancel")
    async def cancel(request: Request, sid: int):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.doctor(c, user)
            slot = c.execute(
                "SELECT * FROM consultation_slots WHERE id=? AND doctor_id=?",
                (sid, user["doctor_id"]),
            ).fetchone()
            if not slot:
                raise HTTPException(404, "Окно не найдено")
            if slot["status"] == "booked":
                raise HTTPException(
                    409, "На это время записан пациент. Отмените приём в его карточке"
                )
            if slot["status"] == "open":
                c.execute(
                    "UPDATE consultation_slots SET status='cancelled',updated_at=? WHERE id=?",
                    (now(), sid),
                )
                service.event(c, user, sid, "cancelled")
                audit(c, user["id"], "consultation_slot_cancelled", None, sid)
        return RedirectResponse("/doctor/calendar#availability", status_code=303)

    @app.post("/patients/{pid}/appointments/{vid}/duration")
    async def duration(request: Request, pid: int, vid: int):
        user = account(request)
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Длительность уточняет врач")
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            visit = c.execute(
                "SELECT * FROM appointments WHERE id=? AND patient_id=?", (vid, pid)
            ).fetchone()
            if not visit:
                raise HTTPException(404, "Приём не найден")
            if visit["ends_at"] or visit["status"] not in ("planned", "ready"):
                raise HTTPException(409, "Длительность уже указана или приём закрыт")
            label = str(form.get("timezone", visit["timezone_label"]))
            start = service.local_start(visit["starts_at"], label)
            end = service.ending_after(start, form.get("duration_minutes"))
            service.conflict(c, user["doctor_id"], start, end, exclude_appointment=vid)
            c.execute(
                "UPDATE appointments SET ends_at=?,timezone_label=? WHERE id=?",
                (service.stamp(end), label, vid),
            )
            audit(c, user["id"], "appointment_duration_set", pid, vid)
        return RedirectResponse("/doctor/calendar#availability", status_code=303)
