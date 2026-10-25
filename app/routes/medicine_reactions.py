"""Explicit sharing and reaction reporting; never automatic changes to treatment."""

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from fastapi import Request
from fastapi.responses import RedirectResponse
from app.services import medicine_reactions as service
from app.services.navigation import local_destination


def register_medicine_reactions(app, db, account, access, csrf_check):
    def target(request, user, pid, form, default="medicines"):
        path = f"/patients/{pid}/" + (
            "assignments" if user["role"] == "doctor" else "profile/assignments"
        )
        supplied = local_destination(request, str(form.get("return_to", "")))
        return (
            supplied
            if supplied and urlsplit(supplied).path == path
            else path + "#" + default
        )

    def saved_target(request, user, pid, form, report_id):
        url = urlsplit(target(request, user, pid, form))
        params = dict(parse_qsl(url.query))
        params["reaction_saved"] = "1"
        return urlunsplit(
            ("", "", url.path, urlencode(params), f"reaction-report-{report_id}")
        )

    @app.post("/patients/{pid}/assignments/medicines/share")
    async def share(request: Request, pid: int):
        access(request, pid)
        user = account(request)
        form = await request.form(max_files=0, max_fields=10)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.share(c, app.state.db_path, user, pid, form)
        return RedirectResponse(
            f"/patients/{pid}/assignments?medicine_sent=1#medicines", status_code=303
        )

    @app.post("/patients/{pid}/assignments/medicines/reactions")
    async def report(request: Request, pid: int):
        user = account(request)
        access(request, pid, user["role"] == "patient")
        form = await request.form(max_files=0, max_fields=25)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            report_id = service.report(c, app.state.db_path, user, pid, form)
        return RedirectResponse(
            saved_target(request, user, pid, form, report_id), status_code=303
        )

    @app.post("/patients/{pid}/assignments/medicines/reactions/review")
    async def review(request: Request, pid: int):
        access(request, pid)
        user = account(request)
        form = await request.form(max_files=0, max_fields=15)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.review(c, app.state.db_path, user, pid, form)
        return RedirectResponse(
            saved_target(request, user, pid, form, service.integer(form, "report_id")),
            status_code=303,
        )
