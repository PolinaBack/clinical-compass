"""Physician-only laboratory. No patient parameter, free text, upload or clinical writes."""

import json
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.experimental_ai import analysis, jobs, sources, store
from app.experimental_ai.ollama import ERRORS


def register_experimental_ai(app, account, csrf_check, render):
    path = store.default_path()
    main_path = app.state.db_path

    @app.exception_handler(store.ExperimentError)
    async def experiment_error(request, exc):
        detail = ERRORS.get(exc.code, ERRORS["internal_error"])
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": detail, "code": exc.code},
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        response = render(request, "error.html", status=409, detail=detail)
        response.status_code = 409
        response.headers["Cache-Control"] = "no-store"
        return response

    def guard(request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(
                403,
                "Эксперимент доступен только врачу и только на встроенном синтетическом сценарии.",
            )
        if set(request.query_params) - {"run_id", "reviewed"}:
            raise HTTPException(
                422,
                "Эксперимент не принимает параметры пациентов или произвольные входные данные.",
            )
        return user

    async def form_data(request, fields):
        form = await request.form(max_files=0, max_fields=8)
        csrf_check(request, str(form.get("csrf", "")))
        if set(form) != fields or any(len(form.getlist(k)) != 1 for k in form):
            raise HTTPException(
                422, "Допускаются только фиксированные параметры эксперимента."
            )
        return form

    @app.get("/doctor/experimental-ai")
    def laboratory(request: Request, run_id: int | None = None):
        guard(request)
        dataset, metrics, runs, selected, reviews, error = (
            None,
            None,
            [],
            None,
            [],
            None,
        )
        try:
            with store.connect(path, main_path=main_path) as c:
                dataset = store.load(c)
                jobs.expire(c)
                runs = [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,status,stage,created_at FROM experiment_runs ORDER BY id DESC LIMIT 30"
                    )
                ]
                row = c.execute(
                    "SELECT * FROM experiment_runs WHERE id=?",
                    (run_id or (runs[0]["id"] if runs else 0),),
                ).fetchone()
                if run_id and not row:
                    raise HTTPException(404, "Такого экспериментального запуска нет")
                selected = jobs.decoded(row)
                if selected:
                    reviews = [
                        dict(r)
                        for r in c.execute(
                            "SELECT * FROM experiment_reviews WHERE run_id=? ORDER BY id",
                            (selected["id"],),
                        )
                    ]
            metrics = analysis.analyze(dataset)
        except store.ExperimentError as exc:
            error = ERRORS.get(exc.code, ERRORS["internal_error"])
        response = render(
            request,
            "experimental_ai.html",
            dataset=dataset,
            metrics=metrics,
            runs=runs,
            selected=selected,
            reviews=reviews,
            error=error,
            error_messages=ERRORS,
            stages=jobs.STAGES,
            issues=jobs.ISSUES,
            sources=sources.catalog(),
            charts=(
                {
                    key: analysis.plot_segments(dataset["observations"], key, maximum)
                    for key, maximum in [
                        ("wellbeing", 10),
                        ("sleep_hours", 24),
                        ("anxiety", 10),
                    ]
                }
                if dataset
                else {}
            ),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/doctor/experimental-ai/run")
    async def start(request: Request):
        user = guard(request)
        await form_data(request, {"csrf"})
        try:
            job_id = jobs.enqueue(path, user["id"], main_path)
        except store.ExperimentError as exc:
            raise HTTPException(
                409, ERRORS.get(exc.code, ERRORS["internal_error"])
            ) from None
        return RedirectResponse(
            f"/doctor/experimental-ai?run_id={job_id}", status_code=303
        )

    @app.get("/api/experimental-ai/runs/{run_id}")
    def status(request: Request, run_id: int):
        guard(request)
        with store.connect(path, main_path=main_path) as c:
            store.load(c)
            jobs.expire(c)
            row = c.execute(
                "SELECT id,status,stage,heartbeat_at,error_code,started_at,finished_at FROM experiment_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Запуск не найден")
            data = dict(row)
        data["stage_label"] = jobs.STAGES.get(data["stage"], "Выполнение")
        data["error_message"] = ERRORS.get(data["error_code"], "")
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/doctor/experimental-ai/export/{run_id}")
    def export(request: Request, run_id: int):
        guard(request)
        with store.connect(path, main_path=main_path) as c:
            dataset = store.load(c)
            run = jobs.decoded(
                c.execute(
                    "SELECT * FROM experiment_runs WHERE id=?", (run_id,)
                ).fetchone()
            )
            if not run:
                raise HTTPException(404, "Запуск не найден")
            reviews = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM experiment_reviews WHERE run_id=?", (run_id,)
                )
            ]
        return JSONResponse(
            dict(mode="synthetic-only", dataset=dataset, run=run, reviews=reviews),
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="experiment-{run_id}.json"',
            },
        )

    @app.post("/doctor/experimental-ai/runs/{run_id}/review")
    async def evaluate(request: Request, run_id: int):
        user = guard(request)
        form = await form_data(
            request, {"csrf", "grounding", "usefulness", "safety", "issue"}
        )
        try:
            with store.connect(path, main_path=main_path) as c:
                store.load(c)
                jobs.review(
                    c,
                    run_id,
                    user["id"],
                    form["grounding"],
                    form["usefulness"],
                    form["safety"],
                    form["issue"],
                )
        except store.ExperimentError:
            raise HTTPException(
                422, "Проверьте фиксированные оценки и завершённость запуска."
            ) from None
        return RedirectResponse(
            f"/doctor/experimental-ai?run_id={run_id}&reviewed=1#expert-review",
            status_code=303,
        )
