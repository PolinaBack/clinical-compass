"""Local AI summaries for any card the signed-in leading doctor may access."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app.services import patient_ai as service
from app.services.patient_ai_data import collect, scope
from app.experimental_ai.analysis import plot_segments
from app.experimental_ai.jobs import STAGES
from app.experimental_ai.sources import catalog


def register_patient_ai(app, db, account, csrf_check, render):
    path = app.state.db_path

    def authorized(request, c, pid):
        user = account(request)
        scope(c, user["id"], pid, user["auth_version"])
        return user

    @app.get("/patients/{pid}/ai-analysis")
    def page(request: Request, pid: int, run_id: int | None = None):
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
