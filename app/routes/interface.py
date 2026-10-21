"""Working entry points over existing records, scoped to the signed-in account."""

from fastapi import Request, HTTPException
from app.services.interface import read_interface


def register_interface(app, db, account, render, patient_context):
    @app.get("/patients/{pid}/reports")
    def reports(request: Request, pid: int):
        response = render(request, "reports.html", **patient_context(request, pid))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/me/overview")
    def patient_overview(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Это раздел пациента")
        pid = user["patient_id"]
        context = patient_context(request, pid)
        diary = max(context["diary"], key=lambda r: (r["day"], r["id"]), default=None)
        assessments = context["assessment_data"]["assessments"]
        completed = [a for a in assessments if a["status"] == "completed"]
        last = max(completed, key=lambda a: (a["completed_at"] or "", a["id"]), default=None)
        visits = context["appointments"]
        latest_visit = visits[0] if visits else None
        definitions = {
            "wellbeing": dict(category="wellbeing", href=f"/patients/{pid}/diary",
                              value=f"{diary['wellbeing']} из 10" if diary else "Нет данных", day=diary["day"] if diary else ""),
            "sleep": dict(category="sleep", href=f"/patients/{pid}/diary",
                          value=f"{diary['sleep']:g} ч" if diary else "Нет данных", day=diary["day"] if diary else ""),
            "treatment": dict(category="medication", href=f"/patients/{pid}/diary",
                              value={"taken": "Приём отмечен", "missed": "Отмечен пропуск", "none": "Нет назначения"}[diary["adherence"]] if diary else "Нет данных",
                              day=diary["day"] if diary else ""),
            "scales": dict(category="scales", href=f"/patients/{pid}/profile/assessments",
                           value=f"{len(completed)} завершено · {len(assessments)-len(completed)} в работе",
                           day=last["completed_at"] if last else ""),
            "materials": dict(category="materials", href="/me/materials", value="Полезные материалы", day=""),
            "consultations": dict(category="consultation", href=f"/patients/{pid}/profile/video",
                                  value=latest_visit["starts_at"] if latest_visit else "Нет назначенных встреч",
                                  day=latest_visit["timezone_label"] if latest_visit else ""),
        }
        with db() as c:
            preferences = read_interface(c, user["id"])
        cards = [dict(item, **definitions[item["key"]]) for item in preferences["cards"] if item["visible"]]
        cards.sort(key=lambda item: (not item["pinned"], item["position"]))
        response = render(request, "overview.html", cards=cards, **context)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/me/materials")
    def materials(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Это раздел пациента")
        return render(request, "materials.html")

    @app.get("/doctor/library")
    def library(request: Request):
        if account(request)["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        return render(request, "library.html")

    @app.get("/doctor/calendar")
    @app.get("/doctor/tasks")
    def doctor_worklist(request: Request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Это раздел специалиста")
        with db() as c:
            def rows(table, order):
                # Names are fixed here, never supplied by the caller or request.
                return [dict(r) for r in c.execute(
                    f"SELECT t.*,p.name FROM {table} t JOIN patients p ON p.id=t.patient_id WHERE p.doctor_id=? ORDER BY {order}",
                    (user["doctor_id"],),
                )]
            appointments = rows("appointments", "t.starts_at,t.id")
            bookings = [r for r in rows("consultation_requests", "t.preferred_day,t.id") if r["status"] == "requested"]
            assessments = [r for r in rows("assessments", "t.due_day,t.id") if r["status"] != "completed"]
        response = render(request, "doctor_worklist.html", calendar=request.url.path.endswith("calendar"),
                          appointments=appointments, bookings=bookings, assignments=assessments)
        response.headers["Cache-Control"] = "no-store"
        return response
