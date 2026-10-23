"""Working entry points over existing records, scoped to the signed-in account."""

from fastapi import Request, HTTPException
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
    def legacy_library(request: Request):
        if account(request)["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        return RedirectResponse("/doctor/resources", status_code=303)

    @app.get("/doctor/calendar")
    @app.get("/doctor/tasks")
    def doctor_worklist(request: Request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        with db() as c:

            def rows(table, order):
                # Names are fixed here, never supplied by the caller or request.
                return [
                    dict(r)
                    for r in c.execute(
                        f"SELECT t.*,p.name FROM {table} t JOIN patients p ON p.id=t.patient_id WHERE p.doctor_id=? ORDER BY {order}",
                        (user["doctor_id"],),
                    )
                ]

            appointments = rows("appointments", "t.starts_at,t.id")
            bookings = [
                r
                for r in rows("consultation_requests", "t.preferred_day,t.id")
                if r["status"] == "requested"
            ]
            assessments = [
                r
                for r in rows("assessments", "t.due_day,t.id")
                if r["status"] != "completed"
            ]
            from app.services.consultations import calendar_context

            availability = calendar_context(c, user)
        response = render(
            request,
            "doctor_worklist.html",
            calendar=request.url.path.endswith("calendar"),
            appointments=appointments,
            bookings=bookings,
            assignments=assessments,
            **availability,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
