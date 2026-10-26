"""Patient request forms, clinician inbox and private document attachments."""

import secrets
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from app.services import extract_requests as service, patient_extracts as documents
from app.services import clinical_attachments as files
from app.services.clinical_forms import today
from app.storage import audit


def register_extract_requests(app, db, account, access, csrf_check, render):
    def root(pid):
        return f"/patients/{pid}/profile/extracts"

    def page(request, pid, data=None, error="", status=200):
        with db() as c:
            user = account(request)
            context = shelf_context(c, app.state.db_path, user, pid)
            patient = dict(
                c.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
            )
            saved = documents.listing(c, app.state.db_path, user, pid)
        if data is not None:
            context["request_form"] = data
        response = render(
            request,
            "patient_extracts.html",
            patient=patient,
            section="extracts",
            documents=saved,
            account_id=user["id"],
            create_token=secrets.token_urlsafe(24),
            request_error=error,
            **context,
        )
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    def detail(request, pid, rid, form=None, error="", status=200):
        with db() as c:
            user = account(request)
            item = service.get(c, app.state.db_path, user, pid, rid)
            patient = dict(
                c.execute("SELECT id,name FROM patients WHERE id=?", (pid,)).fetchone()
            )
        response = render(
            request,
            "extract_request.html",
            patient=patient,
            section="extracts",
            item=item,
            request_kinds=service.KINDS,
            request_purposes=service.PURPOSES,
            request_due=service.DUE,
            request_states=service.STATES,
            reply_form=dict(form or {}),
            reply_token=(
                str(form.get("client_token", "")) if form else secrets.token_urlsafe(24)
            ),
            request_error=error,
            account_id=user["id"],
        )
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/doctor/extract-requests")
    def inbox(request: Request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Раздел доступен специалисту")
        with db() as c:
            items = []
            for row in c.execute(
                "SELECT r.id,r.patient_id,p.name FROM extract_requests r JOIN patients p ON p.id=r.patient_id WHERE p.doctor_id=? ORDER BY CASE WHEN r.status IN ('ready','declined') THEN 1 ELSE 0 END,r.updated_at DESC,r.id DESC",
                (user["doctor_id"],),
            ):
                item = service.get(
                    c, app.state.db_path, user, row["patient_id"], row["id"]
                )
                items.append(dict(item, patient_name=row["name"]))
        response = render(
            request,
            "extract_request_inbox.html",
            items=items,
            request_kinds=service.KINDS,
            request_states=service.STATES,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/patients/{pid}/profile/extracts/requests/new")
    async def create_request(request: Request, pid: int):
        access(request, pid, True)
        form = await request.form(
            max_files=1, max_fields=400, max_part_size=float("inf")
        )
        csrf_check(request, str(form.get("csrf", "")))
        try:
            prepared = await service.uploads(form)
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                ids = service.submit_many(
                    c, app.state.db_path, account(request), pid, form, prepared
                )
        except HTTPException as exc:
            if exc.status_code not in (409, 422):
                raise
            try:
                data = service.batch_form(form)
            except HTTPException:
                data = service.candidate(form)
            data["client_token"] = str(
                form.get("client_token", "")
            ) or secrets.token_urlsafe(24)
            return page(
                request,
                pid,
                data,
                str(exc.detail)
                + (
                    " Если был выбран файл, выберите его повторно."
                    if form.get("file")
                    else ""
                ),
                exc.status_code,
            )
        target = root(pid) + (
            f"/requests/{ids[0]}?sent=1"
            if len(ids) == 1
            else f"?sent_count={len(ids)}#document-requests"
        )
        return RedirectResponse(target, status_code=303)

    @app.get("/patients/{pid}/profile/extracts/requests/{rid}")
    def request_detail(request: Request, pid: int, rid: int):
        access(request, pid)
        return detail(request, pid, rid)

    @app.post("/patients/{pid}/profile/extracts/requests/{rid}/reply")
    async def reply(request: Request, pid: int, rid: int):
        access(request, pid)
        form = await request.form(
            max_files=1, max_fields=15, max_part_size=float("inf")
        )
        csrf_check(request, str(form.get("csrf", "")))
        try:
            prepared = await service.uploads(form)
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                service.reply(
                    c, app.state.db_path, account(request), pid, rid, form, prepared
                )
        except HTTPException as exc:
            if exc.status_code not in (409, 422):
                raise
            return detail(
                request,
                pid,
                rid,
                form,
                str(exc.detail)
                + (
                    " Если был выбран файл, выберите его повторно."
                    if form.get("file")
                    else ""
                ),
                exc.status_code,
            )
        return RedirectResponse(
            root(pid) + f"/requests/{rid}?replied=1", status_code=303
        )

    @app.post("/patients/{pid}/profile/extracts/requests/{rid}/draft")
    async def draft(request: Request, pid: int, rid: int):
        access(request, pid)
        form = await request.form(max_files=0, max_fields=5)
        csrf_check(request, str(form.get("csrf", "")))
        try:
            intent = str(form.get("intent", "edit"))
            if intent not in ("edit", "print"):
                raise HTTPException(422, "Выберите оформление или печать документа")
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                eid = service.make_draft(
                    c, app.state.db_path, account(request), pid, rid, form
                )
        except HTTPException as exc:
            if exc.status_code not in (409, 422):
                raise
            return detail(
                request, pid, rid, error=str(exc.detail), status=exc.status_code
            )
        return RedirectResponse(
            root(pid)
            + f"/{eid}/"
            + ("print?autoprint=1" if intent == "print" else "edit"),
            status_code=303,
        )

    def scoped_file(c, request, pid, rid, fid):
        user = account(request)
        service.get(c, app.state.db_path, user, pid, rid)
        row = c.execute(
            "SELECT * FROM extract_request_files WHERE id=? AND request_id=?",
            (fid, rid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Файл не найден")
        return row

    @app.get("/patients/{pid}/profile/extracts/requests/{rid}/files/{fid}")
    def attachment(request: Request, pid: int, rid: int, fid: int):
        access(request, pid)
        with db() as c:
            row = scoped_file(c, request, pid, rid, fid)
        response = render(
            request,
            "extract_request_file.html",
            file=dict(row),
            file_base=root(pid) + f"/requests/{rid}/files/{fid}",
            return_to=root(pid) + f"/requests/{rid}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/patients/{pid}/profile/extracts/requests/{rid}/files/{fid}/content")
    def attachment_content(
        request: Request,
        pid: int,
        rid: int,
        fid: int,
        download: bool = False,
        ack_unscanned: bool = False,
    ):
        access(request, pid)
        with db() as c:
            row = scoped_file(c, request, pid, rid, fid)
            if row["scan_status"] == "infected":
                raise HTTPException(403, "Файл заблокирован антивирусом")
            if row["scan_status"] != "clean" and not (download and ack_unscanned):
                return RedirectResponse(
                    root(pid) + f"/requests/{rid}/files/{fid}", status_code=303
                )
            content = files.decrypt(c, app.state.db_path, row)
            audit(c, account(request)["id"], "extract_request_file_opened", pid, rid)
        return Response(
            content,
            media_type="application/octet-stream" if download else row["mime"],
            headers={
                "Content-Disposition": ("attachment" if download else "inline")
                + "; filename*=UTF-8''"
                + quote(row["filename"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )


def shelf_context(c, path, user, pid):
    patient = c.execute("SELECT doctor_id FROM patients WHERE id=?", (pid,)).fetchone()
    doctor = (
        c.execute(
            "SELECT name FROM doctors WHERE id=?", (patient["doctor_id"],)
        ).fetchone()
        if patient
        else None
    )
    initial = service.prefill(c, user, pid) if user["role"] == "patient" else {}
    initial["client_token"] = secrets.token_urlsafe(24)
    return dict(
        document_requests=service.listing(c, path, user, pid),
        request_form=initial,
        request_kinds=service.KINDS,
        request_purposes=service.PURPOSES,
        request_due=service.DUE,
        request_states=service.STATES,
        request_today=today().isoformat(),
        request_file_mb=files.file_limit() // (1024 * 1024),
        request_doctor=doctor["name"] if doctor else "Специалист не закреплён",
    )
