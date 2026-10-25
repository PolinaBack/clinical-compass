"""Local AI summaries for any card the signed-in leading doctor may access."""

from fastapi import HTTPException, Query, Request
import secrets
from fastapi.responses import JSONResponse, RedirectResponse
from app.services import patient_ai as service
from app.services.patient_ai_data import collect, scope
from app.experimental_ai.analysis import plot_segments
from app.experimental_ai.jobs import STAGES
from app.experimental_ai.sources import catalog
from app.services import aviandr_analysis, aviandr_data, aviandr_sources, aviandr_jobs
from app.storage import now


def register_patient_ai(app, db, account, csrf_check, render):
    path = app.state.db_path

    def authorized(request, c, pid):
        user = account(request)
        scope(c, user["id"], pid, user["auth_version"])
        return user

    def aviandr_view(c, user, pid, ident=None, report=None):
        report = report or aviandr_analysis.build(c, path, user, pid)
        history = [
            dict(r)
            for r in c.execute(
                "SELECT id,status,created_at FROM aviandr_runs WHERE patient_id=? ORDER BY id DESC LIMIT 30",
                (pid,),
            )
        ]
        selected_id = ident or (history[0]["id"] if history else 0)
        row = c.execute(
            "SELECT * FROM aviandr_runs WHERE id=? AND patient_id=?",
            (selected_id, pid),
        ).fetchone()
        if ident and not row:
            raise HTTPException(404, "Объяснение не найдено в этой карте")
        selected = service.decoded(c, path, row)
        return dict(
            av_report=report,
            av_calculated_at=now(),
            av_selected=selected,
            av_history=history,
            av_stale=bool(selected and selected["snapshot_hash"] != report["checksum"]),
            av_sources=aviandr_sources.active_sources(c),
            av_drugs=aviandr_sources.library.search(c),
            av_messages=aviandr_jobs.MESSAGES,
            av_kinds=aviandr_data.KINDS,
            av_states=aviandr_data.STATES,
            av_therapy=aviandr_data.THERAPY,
            av_relations=aviandr_data.medicines.RELATIONS,
            av_rule_kinds=aviandr_sources.KINDS,
            av_rule_facts=aviandr_sources.FACTS,
            av_operators=aviandr_sources.OPERATORS,
            av_token=secrets.token_urlsafe(24),
            av_today=aviandr_data.today().isoformat(),
        )

    @app.get("/patients/{pid}/ai-analysis")
    def page(
        request: Request,
        pid: int,
        run_id: int | None = None,
        aviandr_id: int | None = None,
    ):
        with db() as c:
            user = authorized(request, c, pid)
            patient = dict(
                c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
            )
            current = collect(c, user["id"], pid, user["auth_version"])
            service.expire(c)
            history = [
                dict(r)
                for r in c.execute(
                    "SELECT id,status,stage,created_at FROM patient_ai_runs WHERE patient_id=? ORDER BY id DESC LIMIT 30",
                    (pid,),
                )
            ]
            selected_id = run_id or (history[0]["id"] if history else 0)
            row = c.execute(
                "SELECT * FROM patient_ai_runs WHERE id=? AND patient_id=?",
                (selected_id, pid),
            ).fetchone()
            if run_id and not row:
                raise HTTPException(404, "Анализ не найден в этой карте")
            selected = service.decoded(c, path, row)
            aviandr_jobs.expire(c)
            av_context = aviandr_view(c, user, pid, aviandr_id)
        from app.experimental_ai.scenarios import digest

        data = selected["snapshot"] if selected else current
        response = render(
            request,
            "patient_ai.html",
            patient=patient,
            section="ai",
            current=current,
            data=data,
            selected=selected,
            history=history,
            stale=bool(selected and selected["snapshot_hash"] != digest(current)),
            stages=STAGES,
            messages=service.MESSAGES,
            sources=catalog(),
            **av_context,
            charts={
                key: plot_segments(data["observations"], key, maximum)
                for key, maximum in [
                    ("wellbeing", 10),
                    ("sleep_hours", 24),
                    ("anxiety", 10),
                ]
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # Register before /aviandr/{ident}; "current" is not a job identifier.
    @app.get("/api/patients/{pid}/ai-analysis/aviandr/current")
    def aviandr_current(
        request: Request,
        pid: int,
        checksum: str = Query(default="", max_length=64),
        aviandr_id: int | None = None,
    ):
        with db() as c:
            user = authorized(request, c, pid)
            if (
                aviandr_id is not None
                and not c.execute(
                    "SELECT id FROM aviandr_runs WHERE id=? AND patient_id=?",
                    (aviandr_id, pid),
                ).fetchone()
            ):
                raise HTTPException(404, "Объяснение не найдено в этой карте")
            report = aviandr_analysis.build(c, path, user, pid)
            value = dict(
                checksum=report["checksum"], changed=checksum != report["checksum"]
            )
            if value["changed"]:
                patient = dict(
                    c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
                )
                context = aviandr_view(c, user, pid, aviandr_id, report)
        if value["changed"]:
            fragment = render(
                request,
                "aviandr_analysis.html",
                patient=patient,
                **context,
                stages=STAGES,
            )
            value["html"] = fragment.body.decode("utf-8")
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @app.post("/patients/{pid}/ai-analysis/run")
    async def run(request: Request, pid: int):
        with db() as c:
            user = authorized(request, c, pid)
        form = await request.form(max_files=0, max_fields=2)
        csrf_check(request, str(form.get("csrf", "")))
        if set(form) != {"csrf"} or len(form.getlist("csrf")) != 1:
            raise HTTPException(
                422, "Анализ использует только сохранённые сведения выбранной карты."
            )
        with db() as c:
            ident = service.enqueue(c, path, user, pid)
        return RedirectResponse(
            f"/patients/{pid}/ai-analysis?run_id={ident}", status_code=303
        )

    @app.get("/api/patients/{pid}/ai-analysis/{run_id}")
    def status(request: Request, pid: int, run_id: int):
        with db() as c:
            authorized(request, c, pid)
            service.expire(c)
            row = c.execute(
                "SELECT id,status,stage,heartbeat_at,error_code FROM patient_ai_runs WHERE id=? AND patient_id=?",
                (run_id, pid),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Анализ не найден в этой карте")
            data = dict(row)
        data.update(
            stage_label=STAGES.get(data["stage"], "Выполнение"),
            error_message=service.MESSAGES.get(data["error_code"], ""),
        )
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/patients/{pid}/ai-analysis/aviandr/run")
    async def aviandr_run(request: Request, pid: int):
        with db() as c:
            user = authorized(request, c, pid)
        form = await request.form(max_files=0, max_fields=3)
        csrf_check(request, str(form.get("csrf", "")))
        if set(form) != {"csrf"} or len(form.getlist("csrf")) != 1:
            raise HTTPException(
                422, "Объяснение использует только рассчитанный сервером снимок."
            )
        with db() as c:
            ident = aviandr_jobs.enqueue(c, path, user, pid)
        return RedirectResponse(
            f"/patients/{pid}/ai-analysis?aviandr_id={ident}#aviandr", status_code=303
        )

    @app.get("/api/patients/{pid}/ai-analysis/aviandr/{ident}")
    def aviandr_status(request: Request, pid: int, ident: int):
        with db() as c:
            authorized(request, c, pid)
            aviandr_jobs.expire(c)
            row = c.execute(
                "SELECT id,status,stage,heartbeat_at,error_code FROM aviandr_runs WHERE id=? AND patient_id=?",
                (ident, pid),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Объяснение не найдено")
        value = dict(row)
        value.update(
            stage_label=STAGES.get(value["stage"], value["stage"]),
            error_message=aviandr_jobs.MESSAGES.get(value["error_code"], ""),
        )
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @app.post("/patients/{pid}/ai-analysis/aviandr/facts")
    async def aviandr_fact(request: Request, pid: int):
        with db() as c:
            user = authorized(request, c, pid)
        form = await request.form(max_files=0, max_fields=25)
        csrf_check(request, str(form.get("csrf", "")))
        kind = str(form.get("kind", ""))
        key = str(form.get("key", ""))
        if kind == "age":
            key = "age"
            value = dict(value=form.get("value", ""))
        elif kind == "therapy":
            key = str(form.get("drug_code", ""))
            value = dict(
                state=form.get("therapy_state", "unknown"),
                start=str(form.get("start", "")),
                inn=str(form.get("inn", "")),
                identity_confirmed=form.get("identity_confirmed") == "yes",
            )
        else:
            value = dict(state=form.get("state", "unknown"))
        with db() as c:
            aviandr_data.add_observation(
                c,
                path,
                user,
                pid,
                kind=kind,
                key=key,
                value=value,
                confirmed=form.get("confirmed") == "yes",
                note=str(form.get("note", "")),
                day=str(form.get("day", "")),
                token=str(form.get("client_token", "")),
            )
        return RedirectResponse(
            f"/patients/{pid}/ai-analysis?av_saved=1#aviandr", status_code=303
        )

    @app.post("/patients/{pid}/ai-analysis/aviandr/rules")
    async def aviandr_rule(request: Request, pid: int):
        with db() as c:
            user = authorized(request, c, pid)
        form = await request.form(max_files=0, max_fields=15)
        csrf_check(request, str(form.get("csrf", "")))
        if form.get("review_ack") != "yes":
            raise HTTPException(
                422,
                "Отметьте подтверждение сверки правила с цитатой — оно находится перед кнопкой сохранения.",
            )
        with db() as c:
            values = dict(form)
            if values.get("fact") == "age":
                values["expected"] = values.get("expected_age", "")
            aviandr_sources.add_rule(c, user, values)
        return RedirectResponse(
            f"/patients/{pid}/ai-analysis?av_rule_saved=1#aviandr", status_code=303
        )

    @app.post("/patients/{pid}/assignments/medicines/intake")
    async def intake_event(request: Request, pid: int):
        user = account(request)
        form = await request.form(max_files=0, max_fields=12)
        csrf_check(request, str(form.get("csrf", "")))
        from app.services.care_orders import scope as medication_scope

        with db() as c:
            medication_scope(c, user, pid)
            value = dict(
                state=str(form.get("state", "")),
                reason=str(form.get("reason", "")),
                draft_id=form.get("draft_id"),
                revision=form.get("revision"),
            )
            aviandr_data.add_observation(
                c,
                path,
                user,
                pid,
                kind="intake_event",
                key=str(form.get("code", "")),
                value=value,
                day=str(form.get("day", "")),
                token=str(form.get("client_token", "")),
            )
        return RedirectResponse(
            f"/patients/{pid}/profile/assignments?intake_saved=1#medicines",
            status_code=303,
        )
