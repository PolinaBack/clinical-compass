"""Working entry points over existing records, scoped to the signed-in account."""

import secrets
from fastapi import Request, HTTPException, Query
from fastapi.responses import RedirectResponse


def register_interface(app, db, account, render, patient_context):
    @app.get("/patients/{pid}/reports")
    def reports(request: Request, pid: int):
        patient_context(request, pid)
        raise HTTPException(
            410, "Раздел отчётов удалён. Итоги встреч доступны в консультациях."
        )

    @app.get("/me/overview")
    def patient_overview(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Это раздел пациента")
        return RedirectResponse(
            f"/patients/{user['patient_id']}/diary", status_code=303
        )

    @app.get("/doctor/library")
    def doctor_library(request: Request):
        if account(request)["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        response = render(request, "doctor_library.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/doctor/tasks")
    def old_tasks(request: Request):
        if account(request)["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        return RedirectResponse("/doctor/forums", status_code=303)

    @app.get("/doctor/forums")
    def forums(request: Request):
        if account(request)["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        response = render(request, "doctor_forums.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/doctor/calendar")
    def doctor_worklist(
        request: Request,
        week: str | None = None,
        view: str = "week",
        patient_id: int | None = Query(None, gt=0, le=2**63 - 1),
        appointment_id: int | None = Query(None, gt=0, le=2**63 - 1),
        meeting_id: int | None = Query(None, gt=0, le=2**63 - 1),
        timezone: str | None = Query(None, max_length=80),
    ):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        from app.services.doctor_calendar import context, location

        with db() as c:
            data = context(
                c,
                user,
                week=week,
                view=view,
                pid=patient_id,
                aid=appointment_id,
                mid=meeting_id,
                timezone=timezone,
            )
            if data["selected_meeting_id"]:
                from app.services.meeting_views import meeting_context

                data.update(meeting_context(c, data["selected_meeting_id"], user))
            data["bookings"] = [
                dict(r)
                for r in c.execute(
                    "SELECT q.*,p.name FROM consultation_requests q JOIN patients p ON p.id=q.patient_id WHERE p.doctor_id=? AND q.status='requested' ORDER BY q.id DESC",
                    (user["doctor_id"],),
                )
            ]
        token = secrets.token_urlsafe(24)
        request.session["calendar_tokens"] = (
            request.session.get("calendar_tokens", []) + [token]
        )[-5:]
        response = render(
            request,
            "doctor_calendar.html",
            calendar_embed=True,
            calendar_token=token,
            calendar_meeting_url=location(
                data["selected_patient"]["id"] if data["selected_patient"] else None,
                week,
                mid=data["selected_meeting_id"],
            ),
            **data,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
