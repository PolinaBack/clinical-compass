"""Separate assignments workspace and patient-visible examination requests."""

import secrets
from urllib.parse import quote
from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from app.services import care_orders as service, clinical_attachments as files
from app.storage import audit


def context(c, path, user, pid):
    orders = service.listing(c, path, user, pid)
    return dict(
        care_orders=orders,
        active_orders=[o for o in orders if o["status"] != "cancelled"],
        cancelled_orders=[o for o in orders if o["status"] == "cancelled"],
        order_kinds=service.KINDS,
        order_catalog=service.CATALOG,
        order_due=service.DUE,
        account_id=user["id"],
    )


def register_care_orders(app, db, account, access, csrf_check, render, patient_context):
    def destination(pid, user):
        return f"/patients/{pid}/" + (
            "assignments" if user["role"] == "doctor" else "profile/assignments"
        )

    def redirect(pid, user, oid, flag):
        return RedirectResponse(
            destination(pid, user) + f"?{flag}=1#order-{oid}", status_code=303
        )

    @app.get("/patients/{pid}/assignments")
    def workspace(request: Request, pid: int, version: int | None = None):
        access(request, pid)
        user = account(request)
        if user["role"] != "doctor":
            return RedirectResponse(destination(pid, user), status_code=303)
        data = patient_context(request, pid)
        with db() as c:
            data.update(context(c, app.state.db_path, user, pid))
            from app.routes.medicine_drafts import draft_context

            data.update(
                draft_context(
                    c,
                    app.state.db_path,
                    user,
                    pid,
                    version=version,
                    add=request.query_params.getlist("add"),
                )
            )
            data["patients"] = c.execute(
                "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name,id",
                (user["doctor_id"],),
            ).fetchall()
            data["saved"] = request.query_params.get("saved") == "1"
        response = render(
            request, "care_orders.html", order_token=secrets.token_urlsafe(24), **data
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/patients/{pid}/assignments/preferences")
    async def preferences(request: Request, pid: int):
        access(request, pid, True)
        user = account(request)
        form = await request.form(max_files=0, max_fields=160)
        csrf_check(request, str(form.get("csrf", "")))
        from app.services import medication_preferences

        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            medication_preferences.save(c, app.state.db_path, user, pid, form)
        flag = (
            "preferences_sent" if form.get("intent") == "send" else "preferences_draft"
        )
        return RedirectResponse(
            f"/patients/{pid}/profile/assignments?{flag}=1#medication-preferences",
            status_code=303,
        )

    @app.post("/patients/{pid}/assignments/preferences/request")
    async def assign_preferences(request: Request, pid: int):
        access(request, pid)
        user = account(request)
        form = await request.form(max_files=0, max_fields=3)
        csrf_check(request, str(form.get("csrf", "")))
        from app.services import medication_preferences

        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            medication_preferences.assign(
                c, user, pid, str(form.get("client_token", ""))
            )
        return RedirectResponse(
            f"/patients/{pid}/assignments?preferences_requested=1#medicines",
            status_code=303,
        )

    @app.post("/patients/{pid}/assignments/orders")
    async def create(request: Request, pid: int):
        access(request, pid)
        user = account(request)
        form = await request.form(max_files=0, max_fields=30)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            oid = service.create(c, app.state.db_path, user, pid, form)
        return redirect(pid, user, oid, "sent")

    @app.post("/patients/{pid}/assignments/orders/{oid}/status")
    async def status(request: Request, pid: int, oid: int):
        user = account(request)
        access(request, pid, user["role"] == "patient")
        form = await request.form(max_files=0, max_fields=10)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.change_status(c, user, pid, oid, form)
        return redirect(pid, user, oid, "updated")

    @app.post("/patients/{pid}/assignments/orders/{oid}/files")
    async def upload(request: Request, pid: int, oid: int):
        user = account(request)
        access(request, pid, user["role"] == "patient")
        form = await request.form(max_files=1, max_fields=10)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            row = service.order(c, user, pid, oid)
            if row["status"] == "cancelled":
                raise HTTPException(409, "Назначение отменено специалистом")
        uploads = form.getlist("file")
        if len(uploads) != 1 or not hasattr(uploads[0], "read"):
            raise HTTPException(422, "Выберите один PDF, PNG или JPG до 700 КБ")
        upload = uploads[0]
        try:
            content = await upload.read(service.FILE_LIMIT + 1)
            if not content or len(content) > service.FILE_LIMIT:
                raise HTTPException(422, "Файл пустой или превышает 700 КБ")
            validation = await run_in_threadpool(
                files.validate, content, upload.filename or ""
            )
            if validation[0] not in ("application/pdf", "image/png", "image/jpeg"):
                raise HTTPException(422, "Нужен PDF, PNG или JPG до 700 КБ")
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                service.save_file(
                    c,
                    app.state.db_path,
                    user,
                    pid,
                    oid,
                    upload,
                    content,
                    form,
                    validation,
                )
        finally:
            await upload.close()
        return redirect(pid, user, oid, "file_sent")

    @app.get("/patients/{pid}/assignments/orders/{oid}/files/{fid}")
    def file_page(request: Request, pid: int, oid: int, fid: int):
        access(request, pid)
        user = account(request)
        with db() as c:
            row = service.scoped_file(c, user, pid, oid, fid)
            audit(c, user["id"], "care_order_file_viewed", pid, oid)
        response = render(
            request,
            "care_order_file.html",
            file=dict(row),
            file_base=f"/patients/{pid}/assignments/orders/{oid}/files/{fid}",
            return_to=destination(pid, user) + f"#order-{oid}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/patients/{pid}/assignments/orders/{oid}/files/{fid}/content")
    def content(
        request: Request,
        pid: int,
        oid: int,
        fid: int,
        download: bool = False,
        ack_unscanned: bool = False,
    ):
        access(request, pid)
        user = account(request)
        with db() as c:
            row = service.scoped_file(c, user, pid, oid, fid)
            if row["scan_status"] == "infected":
                raise HTTPException(
                    403, "Антивирус обнаружил угрозу. Скачивание заблокировано."
                )
            if row["scan_status"] != "clean" and not (download and ack_unscanned):
                raise HTTPException(
                    409,
                    "Антивирусная проверка не выполнена. Используйте явное скачивание на странице файла.",
                )
            data = files.decrypt(c, app.state.db_path, row)
            audit(c, user["id"], "care_order_file_opened", pid, oid)
        return Response(
            data,
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

    @app.post("/patients/{pid}/assignments/orders/{oid}/files/{fid}/delete")
    async def delete_file(request: Request, pid: int, oid: int, fid: int):
        user = account(request)
        access(request, pid, user["role"] == "patient")
        form = await request.form(max_files=0, max_fields=10)
        csrf_check(request, str(form.get("csrf", "")))
        if service.field(form, "confirmed", 10) != "yes":
            raise HTTPException(422, "Подтвердите удаление своего файла")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = service.scoped_file(c, user, pid, oid, fid)
            if row["owner_id"] != user["id"]:
                raise HTTPException(403, "Можно удалить только свой файл")
            from app.storage import now

            c.execute(
                "UPDATE care_order_files SET content=NULL,deleted_at=? WHERE id=?",
                (now(), fid),
            )
            audit(c, user["id"], "care_order_file_deleted", pid, oid)
        return redirect(pid, user, oid, "updated")
