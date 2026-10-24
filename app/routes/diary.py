from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from app.services import diary_questions as questions
from app.services.intake import check_scope
from app.storage import audit


def register_diary(app, db, account, access, csrf_check, render, patient_context):
    def doctor(request, pid):
        if access(request, pid) != "doctor":
            raise HTTPException(
                403, "Добавлять и менять вопросы может только ведущий специалист"
            )

    @app.get("/patients/{pid}/diary/questions/new")
    def new_question(request: Request, pid: int):
        doctor(request, pid)
        return render(
            request,
            "diary_question_builder.html",
            question=None,
            condition_groups=questions.condition_groups(),
            formats=questions.FORMATS,
            modules=questions.MODULES,
            **patient_context(request, pid),
        )

    @app.get("/patients/{pid}/diary/questions/{mid}/edit")
    def edit_question(request: Request, pid: int, mid: int):
        doctor(request, pid)
        with db() as c:
            question = next(
                (q for q in questions.definitions(c, pid, True) if q["id"] == mid), None
            )
        if not question:
            raise HTTPException(404, "Вопрос не найден")
        return render(
            request,
            "diary_question_builder.html",
            question=question,
            condition_groups=questions.condition_groups(question),
            formats=questions.FORMATS,
            modules=questions.MODULES,
            **patient_context(request, pid),
        )

    @app.post("/patients/{pid}/diary/questions/preview")
    async def preview(request: Request, pid: int):
        doctor(request, pid)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        definition = questions.configuration(form)
        return render(
            request,
            "diary_question_preview.html",
            question=definition,
            formats=questions.FORMATS,
            modules=questions.MODULES,
            **patient_context(request, pid),
        )

    @app.post("/patients/{pid}/diary/metrics")
    async def add_metric(request: Request, pid: int):
        doctor(request, pid)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            questions.save_definition(c, pid, account(request), form)
        return RedirectResponse(
            f"/patients/{pid}/diary#assigned-metrics", status_code=303
        )

    @app.post("/patients/{pid}/diary/questions/{mid}/save")
    async def update_question(request: Request, pid: int, mid: int):
        doctor(request, pid)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            questions.save_definition(c, pid, account(request), form, mid)
        return RedirectResponse(
            f"/patients/{pid}/diary#assigned-metrics", status_code=303
        )

    @app.post("/patients/{pid}/diary/questions/{mid}/active")
    async def active(request: Request, pid: int, mid: int):
        doctor(request, pid)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        if form.get("active") not in ("yes", "no"):
            raise HTTPException(422, "Выберите состояние вопроса")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            user = account(request)
            check_scope(c, user, pid, doctor=True)
            question = next(
                (q for q in questions.definitions(c, pid, True) if q["id"] == mid), None
            )
            if not question:
                raise HTTPException(404, "Вопрос не найден")
            if str(form.get("revision", "")) != str(question["version"]):
                raise HTTPException(409, "Вопрос изменился")
            questions.ensure_version(c, question, user)
            import json

            definition = {
                key: value
                for key, value in question.items()
                if key
                not in (
                    "id",
                    "patient_id",
                    "created_by",
                    "created_at",
                    "active",
                    "version",
                )
            }
            definition["active_state"] = form["active"] == "yes"
            version = question["version"] + 1
            from app.storage import now

            c.execute(
                "INSERT INTO diary_question_versions VALUES(?,?,?,?,?)",
                (
                    mid,
                    version,
                    json.dumps(definition, ensure_ascii=False),
                    user["id"],
                    now(),
                ),
            )
            c.execute(
                "UPDATE diary_question_config SET version=?,active=? WHERE metric_id=?",
                (version, int(form["active"] == "yes"), mid),
            )
            audit(
                c,
                user["id"],
                (
                    "diary_question_activated"
                    if form["active"] == "yes"
                    else "diary_question_paused"
                ),
                pid,
                mid,
            )
        return RedirectResponse(
            f"/patients/{pid}/diary#assigned-metrics", status_code=303
        )
