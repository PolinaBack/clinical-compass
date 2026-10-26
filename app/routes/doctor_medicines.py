"""Manual or public-page-assisted creation of ordinary medicine cards."""

import secrets
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from app.services import doctor_medicines as service, medicine_web


def register_doctor_medicines(app, db, account, doctor_access, csrf_check, render):
    def page(request, values=None, error=None, excerpt="", status=200):
        response = render(
            request,
            "medicine_new.html",
            values=values or {},
            error=error,
            excerpt=excerpt,
            metadata=service.METADATA,
            sections=medicine_web.SECTIONS,
            client_token=secrets.token_urlsafe(24),
        )
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/medicines/new")
    def new(request: Request):
        doctor_access(request)
        return page(request)

    @app.post("/medicines/new/fetch")
    async def fetch(request: Request):
        doctor_access(request)
        form = await request.form(max_files=0, max_fields=3)
        csrf_check(request, str(form.get("csrf", "")))
        if set(form) - {"csrf", "source_url"} or any(
            len(form.getlist(k)) != 1 for k in form
        ):
            raise HTTPException(422, "Укажите одну ссылку на страницу препарата")
        url = str(form.get("source_url", "")).strip()
        try:
            html, final_url = await run_in_threadpool(medicine_web.fetch, url)
            values, excerpt = await run_in_threadpool(medicine_web.parse, html)
            return page(request, {**values, "source_url": final_url}, excerpt=excerpt)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            return page(request, {"source_url": url}, error=exc.detail, status=422)

    @app.post("/medicines/new/save")
    async def save(request: Request):
        doctor_access(request)
        user = account(request)
        form = await request.form(max_files=0, max_fields=40)
        csrf_check(request, str(form.get("csrf", "")))
        try:
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                code = service.save(c, user, form)
        except HTTPException as exc:
            if exc.status_code not in (409, 422):
                raise
            return page(request, dict(form), error=exc.detail, status=exc.status_code)
        return RedirectResponse("/medicines/" + code.lower(), status_code=303)

    @app.post("/medicines/{code}/delete")
    async def remove(request: Request, code: str):
        doctor_access(request)
        form = await request.form(max_files=0, max_fields=2)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.remove(c, account(request), code.upper())
        request.session["medicine_comparison"] = [
            item
            for item in request.session.get("medicine_comparison", [])
            if item != code.upper()
        ]
        return RedirectResponse("/medicines?deleted=1", status_code=303)
