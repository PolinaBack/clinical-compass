"""Private educational schemes; no publish, sign, order, or patient endpoint."""

from urllib.parse import urlencode
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from app.services import drug_library as library, medicine_drafts as drafts
from app.services import adverse_reactions


def draft_context(c, path, user, patient_id, *, version=None, add=()):
    patient = drafts.patient(c, user, patient_id)
    current = drafts.read(c, path, user, patient_id, version)
    drugs = library.search(c)
    tolerability = (
        current.get("tolerability", {})
        if version is not None
        else adverse_reactions.preference_snapshot(c, patient_id, path)
    )
    registry = adverse_reactions.records(c)
    matches = {
        d["code"]: adverse_reactions.matches(registry, d["code"], tolerability)
        for d in drugs
    }
    checks = (
        {
            d["code"]: adverse_reactions.preference_check(
                registry, d["code"], tolerability
            )
            for d in drugs
        }
        if version is None
        else {}
    )
    return_params = [("patient_id", patient_id)]
    if version is not None:
        return_params.append(("version", version))
    card_return = "/medicines/prescriptions?" + urlencode(return_params)
    for drug in drugs:
        drug["card_url"] = (
            "/medicines/" + drug["slug"] + "?" + urlencode(dict(return_to=card_return))
        )
    if version is not None:
        matches = {
            item["code"]: item.get("preference_matches", [])
            for item in current["items"]
        }
    selected = {item["code"]: item for item in current["items"]}
    for code in add:
        if code not in {d["code"] for d in drugs}:
            raise HTTPException(422, "Препарат отсутствует в справочнике")
        if version is None:
            selected.setdefault(code, dict(code=code))
    from app.services.medicine_reactions import doctor_context

    return dict(
        selected_patient=patient,
        draft=current,
        selected=selected,
        drugs=drugs,
        frequencies=drafts.FREQUENCIES,
        foods=drafts.FOODS,
        routes=drafts.ROUTES,
        readonly=version is not None,
        tolerability=tolerability,
        tolerability_choices=adverse_reactions.preference_choices(tolerability),
        tolerability_matches=matches,
        tolerability_checks=checks,
        **doctor_context(c, path, user, patient_id, current),
    )


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
            if patient_id is not None:
                drafts.patient(c, user, patient_id)
        if patient_id is None:
            return render(
                request,
                "medicine_prescriptions.html",
                patients=patients,
                selected_patient=None,
            )
        params = [("add", code) for code in request.query_params.getlist("add")]
        if version is not None:
            params.append(("version", str(version)))
        if request.query_params.get("saved") == "1":
            params.append(("saved", "1"))
        return RedirectResponse(
            f"/patients/{patient_id}/assignments"
            + ("?" + urlencode(params) if params else "")
            + "#medicines",
            status_code=303,
        )

    @app.post("/medicines/prescriptions/save")
    @app.post("/patients/{pid}/assignments/medicines")
    async def save(request: Request, pid: int | None = None):
        doctor_access(request)
        user = account(request)
        form = await request.form(max_files=0, max_fields=500)
        csrf_check(request, str(form.get("csrf", "")))
        if len(form.multi_items()) > 500 or any(
            len(form.getlist(key)) != 1 for key in ("patient_id", "revision", "intent")
        ):
            raise HTTPException(422, "Некорректная форма черновика")
        try:
            patient_id = int(form.get("patient_id", ""))
        except (ValueError, TypeError):
            raise HTTPException(422, "Выберите учебную карту") from None
        if pid is not None and pid != patient_id:
            raise HTTPException(
                422,
                "Форма относится к другой карте. Откройте назначения нужного пациента.",
            )
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
            drafts.save(c, app.state.db_path, user, patient_id, form)
        destination = f"/patients/{patient_id}/assignments?saved=1#medicines"
        if intent == "check":
            destination = "/medicines/combinations?" + urlencode(
                [("drug", code) for code in codes]
                + [("draft_patient", str(patient_id))]
            )
        return RedirectResponse(destination, status_code=303)
