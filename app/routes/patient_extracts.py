"""Patient document shelf and the leading clinician's review/print editor."""

import secrets
from fastapi import HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from app.services import patient_extracts as service
from app.services.clinical_forms import today
from app.storage import audit


def register_patient_extracts(app, db, account, access, csrf_check, render):
    def root(pid):
        return f"/patients/{pid}/profile/extracts"

    def editor(
        request,
        pid,
        doc,
        data=None,
        *,
        error="",
        status=200,
        conflict=False,
        form_revision=None,
    ):
        data = data or doc["data"]
        try:
            calculations = service.calculations(data)
        except (ValueError, TypeError, ArithmeticError, HTTPException):
            calculations = dict(
                age=None,
                bmi=None,
                bmi_note="Проверьте дату рождения и измерения в форме",
                as_of=today().isoformat(),
            )
        response = render(
            request,
            "patient_extract_editor.html",
            patient=dict(id=pid, name=data["source"]["patient_name"]),
            section="extracts",
            document=doc,
            document_data=data,
            calculations=calculations,
            document_titles=service.TITLES,
            diagnosis_states=service.DIAGNOSES,
            document_error=error,
            conflict=conflict,
            form_revision=form_revision or doc["revision"],
            today=today().isoformat(),
        )
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/patients/{pid}/profile/extracts")
    def extracts(request: Request, pid: int):
        access(request, pid)
        with db() as c:
            user = account(request)
            documents = service.listing(c, app.state.db_path, user, pid)
            patient = dict(
                c.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
            )
        response = render(
            request,
            "patient_extracts.html",
            patient=patient,
            section="extracts",
            documents=documents,
            account_id=user["id"],
            create_token=secrets.token_urlsafe(24),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/patients/{pid}/profile/extracts/new")
    async def create(request: Request, pid: int):
        access(request, pid)
        form = await request.form(max_files=0, max_fields=5)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            ident = service.create(
                c,
                app.state.db_path,
                account(request),
                pid,
                str(form.get("client_token", "")),
            )
        return RedirectResponse(root(pid) + f"/{ident}/edit", status_code=303)

    @app.get("/patients/{pid}/profile/extracts/{eid}/edit")
    def edit(request: Request, pid: int, eid: int):
        access(request, pid)
        with db() as c:
            doc = service.read(
                c, app.state.db_path, account(request), pid, eid, edit=True
            )
        return editor(request, pid, doc)

    @app.post("/patients/{pid}/profile/extracts/{eid}/save")
    async def save(request: Request, pid: int, eid: int):
        access(request, pid)
        user = account(request)
        form = await request.form(
            max_files=0, max_fields=20000, max_part_size=float("inf")
        )
        csrf_check(request, str(form.get("csrf", "")))
        try:
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                doc = service.read(c, app.state.db_path, user, pid, eid, edit=True)
                if str(form.get("revision", "")) != str(doc["revision"]):
                    raise HTTPException(
                        409,
                        "Документ уже изменён в другом окне. Ваш текст показан ниже; откройте актуальную версию перед сохранением",
                    )
                intent = str(form.get("intent", "save"))
                if intent not in (
                    "save",
                    "preview",
                    "refresh",
                    "publish",
                    "print",
                    "withdraw",
                ):
                    raise HTTPException(422, "Выберите действие с документом")
                if intent == "preview":
                    data = service.validate(service.candidate(doc["data"], form))
                    return editor(request, pid, doc, data)
                if intent == "refresh":
                    service.refresh(c, app.state.db_path, user, pid, eid, form)
                elif intent == "withdraw":
                    service.withdraw(c, user, pid, eid, form)
                else:
                    if intent == "print":
                        service.validate(
                            service.candidate(doc["data"], form), publish=True
                        )
                    revision = service.save(
                        c,
                        app.state.db_path,
                        user,
                        pid,
                        eid,
                        form,
                        publish=intent == "publish",
                    )
        except HTTPException as error:
            if error.status_code not in (409, 422):
                raise
            with db() as c:
                doc = service.read(c, app.state.db_path, user, pid, eid, edit=True)
            try:
                data = service.candidate(doc["data"], form)
            except HTTPException:
                data = doc["data"]
            return editor(
                request,
                pid,
                doc,
                data,
                error=str(error.detail),
                status=error.status_code,
                conflict=error.status_code == 409,
                form_revision=str(form.get("revision", "")),
            )
        if intent == "print":
            target = root(pid) + f"/{eid}/print?revision={revision}"
        else:
            target = (
                root(pid)
                + f"/{eid}/edit?saved=1"
                + ("&refreshed=1" if intent == "refresh" else "")
            )
        return RedirectResponse(target, status_code=303)

    @app.get("/patients/{pid}/profile/extracts/{eid}")
    def view(request: Request, pid: int, eid: int):
        access(request, pid)
        user = account(request)
        with db() as c:
            doc = service.read(c, app.state.db_path, user, pid, eid)
            patient = dict(
                c.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
            )
            audit(c, user["id"], "patient_extract_viewed", pid, eid)
        response = render(
            request,
            "patient_extract_view.html",
            patient=patient,
            section="extracts",
            document=doc,
            document_data=doc["data"],
            calculations=service.calculations(doc["data"]),
            diagnosis_states=service.DIAGNOSES,
            account_id=user["id"],
            print_page=False,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/patients/{pid}/profile/extracts/{eid}/print")
    def print_document(
        request: Request, pid: int, eid: int, revision: int | None = Query(None, gt=0)
    ):
        access(request, pid)
        user = account(request)
        with db() as c:
            doc = service.read(c, app.state.db_path, user, pid, eid, revision=revision)
            if not doc["data"]["reviewed"]:
                if user["role"] == "doctor" and doc["author_id"] == user["id"]:
                    doc = service.read(c, app.state.db_path, user, pid, eid, edit=True)
                    return editor(
                        request,
                        pid,
                        doc,
                        error="Подтвердите заполненные сведения в форме ниже, затем нажмите «Сохранить и печатать».",
                    )
                raise HTTPException(404, "Готовая выписка не найдена")
            patient = dict(
                c.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
            )
            audit(c, user["id"], "patient_extract_print_view", pid, eid)
        response = render(
            request,
            "patient_extract_view.html",
            patient=patient,
            section="extracts",
            document=doc,
            document_data=doc["data"],
            calculations=service.calculations(doc["data"]),
            diagnosis_states=service.DIAGNOSES,
            account_id=user["id"],
            print_page=True,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
