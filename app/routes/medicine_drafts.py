"""Private educational schemes; no publish, sign, order, or patient endpoint."""

from urllib.parse import urlencode
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from app.services import drug_library as library, medicine_drafts as drafts


def register_medicine_drafts(app, db, account, doctor_access, csrf_check, render):
    @app.get("/medicines/prescriptions")
    def page(
        request: Request, patient_id: int | None = None, version: int | None = None
    ):
        doctor_access(request)
        user = account(request)
        with db() as c:
            patients = c.execute(
                "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name,id",
                (user["doctor_id"],),
            ).fetchall()
            if patient_id is None and patients:
                patient_id = patients[0]["id"]
            patient = (
                drafts.patient(c, user, patient_id) if patient_id is not None else None
            )
            current = (
                drafts.read(c, app.state.db_path, user, patient_id, version)
                if patient
                else dict(revision=0, items=[], history=[])
            )
            drugs = library.search(c)
        selected = {item["code"]: item for item in current["items"]}
        for code in request.query_params.getlist("add"):
            if code not in {d["code"] for d in drugs}:
                raise HTTPException(422, "Препарат отсутствует в справочнике")
            if version is None:
                selected.setdefault(code, dict(code=code))
        response = render(
            request,
            "medicine_prescriptions.html",
            patients=patients,
            selected_patient=patient,
            draft=current,
            selected=selected,
            drugs=drugs,
            frequencies=drafts.FREQUENCIES,
            foods=drafts.FOODS,
            routes=drafts.ROUTES,
            readonly=version is not None,
            saved=request.query_params.get("saved") == "1",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/medicines/prescriptions/save")
    async def save(request: Request):
        doctor_access(request)
        user = account(request)
        form = await request.form(max_files=0, max_fields=500)
        csrf_check(request, str(form.get("csrf", "")))
        if len(form.multi_items()) > 500 or any(
            len(form.getlist(key)) != 1 for key in ("patient_id", "revision", "intent")
        ):
            raise HTTPException(422, "Некорректная форма черновика")
        try:
            pid = int(form.get("patient_id", ""))
        except (ValueError, TypeError):
            raise HTTPException(422, "Выберите учебную карту") from None
        intent = form.get("intent")
        if intent not in ("save", "check"):
            raise HTTPException(422, "Неизвестное действие с черновиком")
        codes = form.getlist("drug")
        if intent == "check" and len(codes) < 2:
            raise HTTPException(
                422, "Для проверки сочетаний добавьте хотя бы два препарата"
            )
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            drafts.save(c, app.state.db_path, user, pid, form)
        destination = "/medicines/prescriptions?" + urlencode(
            dict(patient_id=pid, saved=1)
        )
        if intent == "check":
            destination = "/medicines/combinations?" + urlencode(
                [("drug", code) for code in codes] + [("draft_patient", str(pid))]
            )
        return RedirectResponse(destination, status_code=303)
