"""Availability management in the existing doctor calendar."""

from datetime import timedelta
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.services import consultations as service
from app.storage import now, audit


def register_consultations(app, db, account, access, csrf_check):
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
            end = (
                start.astimezone(service.UTC)
                + timedelta(minutes=service.duration(form.get("duration_minutes")))
            ).astimezone(start.tzinfo)
            service.conflict(c, user["doctor_id"], start, end, exclude_appointment=vid)
            c.execute(
                "UPDATE appointments SET ends_at=?,timezone_label=? WHERE id=?",
                (service.stamp(end), label, vid),
            )
            audit(c, user["id"], "appointment_duration_set", pid, vid)
        return RedirectResponse("/doctor/calendar#availability", status_code=303)
