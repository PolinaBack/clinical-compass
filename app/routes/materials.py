from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.services import materials as service
from app.storage import now, audit


def register_materials(app, db, account, csrf_check, render):
    def reply(pid=None):
        return RedirectResponse(
            "/doctor/resources" + (f"?patient_id={pid}" if pid else ""), status_code=303
        )

    @app.get("/doctor/resources")
    def library(request: Request, patient_id: int | None = None):
        user = account(request)
        with db() as c:
            resources = service.items(c, user)
            if patient_id:
                service.check_scope(c, user, patient_id, doctor=True)
            patients = [
                dict(r)
                for r in c.execute(
                    "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name",
                    (user["doctor_id"],),
                )
            ]
            assignments = [
                dict(r)
                for r in c.execute(
                    "SELECT a.*,p.name patient_name,d.name doctor_name FROM resource_assignments a JOIN patients p ON p.id=a.patient_id JOIN accounts u ON u.id=a.assigned_by JOIN doctors d ON d.id=u.doctor_id WHERE p.doctor_id=? ORDER BY a.id DESC",
                    (user["doctor_id"],),
                )
            ]
        return render(
            request,
            "resource_library.html",
            resource_items=resources,
            resource_patients=patients,
            resource_assignments=assignments,
            selected_patient=patient_id,
            resource_status=service.STATUS,
        )

    @app.post("/doctor/resources")
    async def create(request: Request):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.create(c, user, form)
        return reply()

    @app.post("/doctor/resources/{rid}/assign")
    async def assign(request: Request, rid: int):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        try:
            pid = int(form.get("patient_id", ""))
        except ValueError:
            raise HTTPException(422, "Выберите пациента") from None
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.assign(c, user, rid, pid, form)
        return reply(pid)

    @app.get("/me/materials")
    def patient_materials(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Это раздел пациента")
        with db() as c:
            service.check_scope(c, user, user["patient_id"])
            assignments = [
                dict(r)
                for r in c.execute(
                    "SELECT r.*,d.name doctor_name FROM resource_assignments r JOIN accounts a ON a.id=r.assigned_by JOIN doctors d ON d.id=a.doctor_id WHERE r.patient_id=? ORDER BY r.id DESC",
                    (user["patient_id"],),
                )
            ]
        return render(
            request,
            "materials.html",
            resource_assignments=assignments,
            resource_status=service.STATUS,
        )

    @app.get("/material-assignments/{aid}/open")
    def open_resource(request: Request, aid: int):
        user = account(request)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            item = service.assignment(c, user, aid)
            if user["role"] == "patient":
                if item["status"] not in ("read", "opened"):
                    c.execute(
                        "UPDATE resource_assignments SET status='opened',updated_at=? WHERE id=?",
                        (now(), aid),
                    )
                    service.event(c, user, aid, "opened")
                audit(c, user["id"], "resource_opened", item["patient_id"], aid)
        return RedirectResponse(
            service.valid_url(item["url"]),
            status_code=303,
            headers={"Referrer-Policy": "no-referrer"},
        )

    @app.post("/material-assignments/{aid}/status")
    async def mark(request: Request, aid: int):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        status = form.get("status")
        if status not in ("read", "later"):
            raise HTTPException(422, "Выберите состояние материала")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            item = service.assignment(c, user, aid, write=True)
            if item["status"] != status:
                c.execute(
                    "UPDATE resource_assignments SET status=?,updated_at=? WHERE id=?",
                    (status, now(), aid),
                )
                service.event(c, user, aid, status)
        return RedirectResponse("/me/materials", status_code=303)
