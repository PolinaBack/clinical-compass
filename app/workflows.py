"""Patient questionnaires and clinician workflow, separate from treatment decisions."""

import json
import secrets
from datetime import date, timedelta, datetime, timezone

from fastapi import Request, Form, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse, Response, FileResponse

from .scales import (
    SCALES,
    VERSION,
    INSTRUCTION,
    OPTIONS,
    HCL_CONTEXT,
    ANSWER_STATES,
    score,
    combined,
    symptom_category,
)
from .storage import now, audit
from .safety import (
    SAFETY_VERSION,
    SAFETY_EXPLANATIONS,
    normalize_safety,
    safety_path,
    answered_value,
)


def overview(db, pid):
    with db() as c:
        assessments = [
            dict(r)
            for r in c.execute(
                "SELECT a.*,s.period_start,s.period_end FROM assessments a JOIN assessment_sessions s ON s.id=a.session_id WHERE a.patient_id=? ORDER BY a.id DESC",
                (pid,),
            )
        ]
        for a in assessments:
            answers = {
                r["item"]: dict(r)
                for r in c.execute(
                    "SELECT * FROM answers WHERE assessment_id=?", (a["id"],)
                )
            }
            a["result"] = score(a["scale"], answers)
            if a["status"] == "draft":
                a["result"]["total"] = None
            a["overdue"] = bool(
                a["due_day"]
                and a["due_day"] < date.today().isoformat()
                and a["status"] != "completed"
            )
            a["category"] = symptom_category(a["scale"], a["result"]["total"])
            a["period_label"] = (
                "1–5: последний месяц; 6: за жизнь; уточнение: последние 3 месяца"
                if a["scale"] == "safety6"
                else (
                    "Периоды состояний подъёма — по инструкции вопросов"
                    if a["scale"] == "hcl33"
                    else f"{a['period_start']} — {a['period_end']}"
                )
            )
            a["name"] = SCALES[a["scale"]]["name"]
            a["topics"] = sum(
                r["discuss"] and not r["discussed"] for r in answers.values()
            )
        events = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM assessment_events WHERE patient_id=? ORDER BY id DESC",
                (pid,),
            )
        ]
    return {"assessments": assessments, "events": events}


def register_workflows(
    app, db, account, identity, access, csrf_check, render, has_consent
):
    def assessment(request, aid, write=False):
        with db() as c:
            row = c.execute(
                "SELECT a.*,s.period_start,s.period_end FROM assessments a JOIN assessment_sessions s ON s.id=a.session_id WHERE a.id=?",
                (aid,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Анкета не найдена")
        access(request, row["patient_id"], write)
        return dict(row)

    @app.get("/project-sources/{slug}")
    def project_source(slug: str):
        files = {"faq": "FAQ.pdf", "additional": "Additional.pdf", "brief": "Brief.pdf"}
        if slug not in files:
            raise HTTPException(404, "Источник не найден")
        from pathlib import Path

        return FileResponse(
            Path(__file__).resolve().parent.parent / "docs" / "inputs" / files[slug],
            media_type="application/pdf",
            filename=files[slug],
        )

    @app.get("/assessments/{aid}/history")
    def history(request: Request, aid: int):
        a = assessment(request, aid)
        with db() as c:
            changes = [
                dict(r)
                for r in c.execute(
                    "SELECT h.*,a.item FROM answer_history h JOIN answers a ON a.id=h.answer_id WHERE a.assessment_id=? ORDER BY h.id DESC",
                    (aid,),
                )
            ]
            audit(
                c, account(request)["id"], "answer_history_view", a["patient_id"], aid
            )
        for row in changes:
            row["previous"] = json.loads(row["payload"])
        return render(request, "history.html", aid=aid, changes=changes)

    @app.get("/faq")
    def faq(request: Request):
        return render(request, "faq.html")

    @app.get("/help-now")
    def help_now(request: Request):
        return render(request, "help.html")

    @app.get("/privacy")
    def privacy(request: Request):
        return render(request, "privacy.html")

    @app.get("/clinical-tools")
    def tools(request: Request, patient_id: int | None = None):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только специалисту")
        with db() as c:
            patients = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM patients WHERE doctor_id=? ORDER BY id",
                    (account(request)["doctor_id"],),
                )
            ]
        selected = None
        data = {"assessments": []}
        if patient_id is not None:
            access(request, patient_id)
            selected = next((p for p in patients if p["id"] == patient_id), None)
            data = overview(db, patient_id)
        return render(
            request,
            "tools.html",
            patients=patients,
            patient=selected,
            assessment_data=data,
            today=date.today().isoformat(),
        )

    @app.get("/consent")
    def consent_page(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Страница пациента")
        return render(request, "consent.html", accepted=has_consent(user["patient_id"]))

    @app.post("/consent")
    def consent_save(
        request: Request,
        csrf: str = Form(...),
        accepted: bool = Form(False),
        adult: bool = Form(False),
    ):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Страница пациента")
        csrf_check(request, csrf)
        if accepted and not adult:
            raise HTTPException(422, "Демонстрационный маршрут предназначен для 18+")
        with db() as c:
            c.execute(
                "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(?,?,?,?,?)",
                (
                    user["patient_id"],
                    "synthetic-v2-2026-10-18",
                    int(accepted),
                    int(adult),
                    now(),
                ),
            )
            audit(
                c,
                user["id"],
                "consent_accepted" if accepted else "consent_withdrawn",
                user["patient_id"],
            )
        return RedirectResponse(
            f"/patients/{user['patient_id']}/safety" if accepted else "/faq",
            status_code=303,
        )

    @app.post("/safety/start")
    def safety_start(request: Request, csrf: str = Form(...)):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Отвечает пациент")
        pid = user["patient_id"]
        access(request, pid, True)
        csrf_check(request, csrf)
        from calendar import monthrange

        today = date.today()
        year, month = (
            (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        )
        month_start = date(year, month, min(today.day, monthrange(year, month)[1]))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT a.id FROM assessments a JOIN assessment_sessions s ON s.id=a.session_id WHERE a.patient_id=? AND a.scale='safety6' AND a.version=? AND s.period_end=? ORDER BY a.id DESC LIMIT 1",
                (pid, SAFETY_VERSION, today.isoformat()),
            ).fetchone()
            if existing:
                aid = existing["id"]
            else:
                sid = c.execute(
                    "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
                    (pid, month_start.isoformat(), today.isoformat(), now()),
                ).lastrowid
                aid = c.execute(
                    "INSERT INTO assessments(session_id,patient_id,scale,version) VALUES(?,?,'safety6',?)",
                    (sid, pid, SAFETY_VERSION),
                ).lastrowid
                audit(c, user["id"], "teaching_safety_started", pid, aid)
        return RedirectResponse(f"/assessments/{aid}", status_code=303)

    @app.post("/assessments/start")
    def start(request: Request, csrf: str = Form(...)):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Начать самоотчёт может пациент")
        access(request, user["patient_id"], True)
        csrf_check(request, csrf)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT id FROM assessments WHERE patient_id=? AND scale='phq4' AND status='draft' ORDER BY id DESC LIMIT 1",
                (user["patient_id"],),
            ).fetchone()
            if existing:
                aid = existing["id"]
            else:
                if c.execute(
                    "SELECT id FROM assessments WHERE patient_id=? AND scale!='safety6' LIMIT 1",
                    (user["patient_id"],),
                ).fetchone():
                    raise HTTPException(
                        409,
                        "Повторные измерения назначает специалист. Продолжайте существующие формы или обсудите новую сессию.",
                    )
                sid = c.execute(
                    "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
                    (
                        user["patient_id"],
                        (date.today() - timedelta(days=13)).isoformat(),
                        date.today().isoformat(),
                        now(),
                    ),
                ).lastrowid
                aid = c.execute(
                    "INSERT INTO assessments(session_id,patient_id,scale,version) VALUES(?,?,'phq4',?)",
                    (sid, user["patient_id"], VERSION),
                ).lastrowid
                audit(c, user["id"], "assessment_started", user["patient_id"], aid)
        return RedirectResponse(f"/assessments/{aid}", status_code=303)

    @app.get("/assessments/{aid}")
    def assessment_page(
        request: Request, aid: int, item: int | None = None, review: bool = False
    ):
        a = assessment(request, aid)
        with db() as c:
            answers = {
                r["item"]: dict(r)
                for r in c.execute(
                    "SELECT * FROM answers WHERE assessment_id=?", (aid,)
                )
            }
            audio = [
                dict(r)
                for r in c.execute(
                    "SELECT id,item,mime,duration,transcript,transcript_status FROM audio_notes WHERE assessment_id=? AND deleted_at IS NULL",
                    (aid,),
                )
            ]
            follow_up = c.execute(
                "SELECT id FROM assessments WHERE patient_id=? AND scale='phq4' ORDER BY id DESC LIMIT 1",
                (a["patient_id"],),
            ).fetchone()
            audit(c, account(request)["id"], "assessment_view", a["patient_id"], aid)
        count = len(SCALES[a["scale"]]["items"])
        if item is None:
            item = next(
                (
                    i
                    for i in range(1, count + 1)
                    if answers.get(str(i), {}).get("state", "unanswered")
                    == "unanswered"
                ),
                1,
            )
        if not 1 <= item <= count:
            raise HTTPException(404, "Вопрос не найден")
        read_only = (
            identity(request) == "doctor" or review or not has_consent(a["patient_id"])
        )
        path = (
            safety_path(answers)
            if a["scale"] == "safety6"
            else list(range(1, count + 1))
        )
        if a["scale"] == "safety6" and not read_only and item not in path:
            return RedirectResponse(f"/assessments/{aid}?item=6", status_code=303)
        return render(
            request,
            "assessment.html" if read_only else "assessment_step.html",
            follow_up=follow_up,
            step=item,
            step_position=path.index(item) + 1 if item in path else item,
            step_count=len(path),
            has_next=item != path[-1],
            safety_explanations=SAFETY_EXPLANATIONS,
            read_only=read_only,
            assessment=a,
            definition=SCALES[a["scale"]],
            instruction=INSTRUCTION,
            options=OPTIONS,
            answers=answers,
            result=score(a["scale"], answers),
            hcl_context=HCL_CONTEXT,
            today=date.today().isoformat(),
            audio=audio,
            request_id=secrets.token_urlsafe(24),
            consent=has_consent(a["patient_id"]),
        )

    @app.post("/assessments/{aid}/save")
    async def save(request: Request, aid: int):
        a = assessment(request, aid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        intent = str(form.get("intent", "draft"))
        if intent not in ["draft", "submit", "next", "back"]:
            raise HTTPException(422, "Неизвестное действие")
        count = len(SCALES[a["scale"]]["items"])
        step_raw = str(form.get("step_item", ""))
        if step_raw and (not step_raw.isdigit() or not 1 <= int(step_raw) <= count):
            raise HTTPException(422, "Неизвестный вопрос")
        step = int(step_raw) if step_raw else None
        if intent in ["next", "back"] and step is None:
            raise HTTPException(422, "Обновите страницу вопроса")
        if step and intent == "next" and step == count:
            raise HTTPException(422, "Это последний вопрос")
        target = (
            f"/assessments/{aid}?item={min(count, max(1, step + (1 if intent == 'next' else -1 if intent == 'back' else 0)))}&saved=1"
            if step
            else f"/assessments/{aid}?saved=1"
        )
        if step and intent == "submit":
            target = f"/assessments/{aid}?review=1&saved=1"
        revision = str(form.get("revision", ""))
        token = str(form.get("request_id", ""))
        if not revision.isdigit() or not 10 <= len(token) <= 80:
            raise HTTPException(422, "Обновите страницу анкеты")

        def answer_value(raw, maximum):
            if raw in ["", *ANSWER_STATES]:
                return None, raw or "unanswered"
            if not raw.isdigit() or not 0 <= int(raw) <= maximum:
                raise HTTPException(422, "Недопустимый ответ")
            return int(raw), "answered"

        parsed = {}
        if step:
            with db() as c:
                parsed = {
                    row["item"]: {
                        key: row[key]
                        for key in ["value", "state", "comment", "discuss"]
                    }
                    for row in c.execute(
                        "SELECT * FROM answers WHERE assessment_id=?", (aid,)
                    )
                }
        for i in [step] if step else range(1, count + 1):
            key = str(i)
            value, state = answer_value(
                str(form.get("answer_" + key, "")),
                1 if a["scale"] in ["hcl33", "safety6"] else 3,
            )
            comment = str(form.get("comment_" + key, ""))
            if len(comment) > 3000:
                raise HTTPException(422, "Комментарий длиннее 3000 символов")
            parsed[key] = {
                "value": value,
                "state": state,
                "comment": comment,
                "discuss": int(form.get("discuss_" + key) == "true"),
            }
        if a["scale"] == "safety6":
            if not step:
                raise HTTPException(422, "Сохраняйте вопросы безопасности по одному")
            with db() as c:
                previous_answers = {
                    r["item"]: dict(r)
                    for r in c.execute(
                        "SELECT * FROM answers WHERE assessment_id=?", (aid,)
                    )
                }
            if step not in safety_path(previous_answers):
                raise HTTPException(409, "Обновите текущий вопрос")
            parsed = normalize_safety(parsed)
            path = safety_path(parsed)
            position = path.index(step)
            if intent in ["next", "submit"]:
                if step == path[-1]:
                    intent = "submit"
                    target = f"/assessments/{aid}?review=1&saved=1"
                else:
                    intent = "next"
                    target = f"/assessments/{aid}?item={path[position+1]}&saved=1"
            elif intent == "back":
                target = f"/assessments/{aid}?item={path[max(0, position-1)]}&saved=1"
        if a["scale"] == "hcl33":
            for index, (key, label, choices) in enumerate(HCL_CONTEXT):
                if step and not (
                    (step == 1 and index < 2) or (step == count and index >= 2)
                ):
                    continue
                raw = str(form.get("context_" + key, ""))
                value, state = answer_value(raw, len(choices) - 1 if choices else 366)
                parsed["ctx_" + key] = {
                    "value": value,
                    "state": state,
                    "comment": "",
                    "discuss": 0,
                }
        if (
            a["scale"] == "hcl33"
            and parsed.get("ctx_year", {}).get("state") == "answered"
            and parsed["ctx_year"]["value"] == 1
        ):
            parsed["ctx_days"] = {
                "value": None,
                "state": "not_asked_by_rule",
                "comment": "",
                "discuss": 0,
            }
        if (
            a["scale"] != "hcl33"
            and a["period_end"] < date.today().isoformat()
            and form.get("period_ack") != "true"
        ):
            raise HTTPException(
                409,
                "Подтвердите, что ответы относятся к указанному периоду, либо начните новую сессию.",
            )
        payload = json.dumps(
            {"assessment": aid, "intent": intent, "step": step, "answers": parsed},
            ensure_ascii=False,
            sort_keys=True,
        )
        user = account(request)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT * FROM save_requests WHERE token=?", (token,)
            ).fetchone()
            if previous:
                if (
                    previous["account_id"] != user["id"]
                    or previous["payload"] != payload
                ):
                    raise HTTPException(
                        409, "Повторная отправка изменённых данных. Обновите страницу."
                    )
                return RedirectResponse(target, status_code=303)
            current = c.execute(
                "SELECT revision FROM assessments WHERE id=?", (aid,)
            ).fetchone()
            if current["revision"] != int(revision):
                raise HTTPException(
                    409,
                    "Анкета изменилась в другой вкладке. Обновите страницу перед сохранением.",
                )
            old_safety = (
                {
                    row["item"]: dict(row)
                    for row in c.execute(
                        "SELECT * FROM answers WHERE assessment_id=?", (aid,)
                    )
                }
                if a["scale"] == "safety6"
                else {}
            )
            old_nine = c.execute(
                "SELECT value,state FROM answers WHERE assessment_id=? AND item='9'",
                (aid,),
            ).fetchone()
            previously_positive = bool(
                old_nine
                and old_nine["state"] == "answered"
                and old_nine["value"] is not None
                and old_nine["value"] > 0
            )
            for key, item in parsed.items():
                old = c.execute(
                    "SELECT * FROM answers WHERE assessment_id=? AND item=?", (aid, key)
                ).fetchone()
                changed = not old or any(old[k] != item[k] for k in item)
                if not changed:
                    continue
                if old:
                    c.execute(
                        "INSERT INTO answer_history(answer_id,payload,account_id,created_at) VALUES(?,?,?,?)",
                        (
                            old["id"],
                            json.dumps(dict(old), ensure_ascii=False),
                            user["id"],
                            now(),
                        ),
                    )
                c.execute(
                    "INSERT INTO answers(assessment_id,item,value,state,comment,discuss,changed_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(assessment_id,item) DO UPDATE SET value=excluded.value,state=excluded.state,comment=excluded.comment,discuss=excluded.discuss,discussed=0,changed_at=excluded.changed_at",
                    (
                        aid,
                        key,
                        item["value"],
                        item["state"],
                        item["comment"],
                        item["discuss"],
                        now(),
                    ),
                )
            result = score(a["scale"], parsed)
            complete = result.get("complete", result["total"] is not None)
            status = (
                "draft"
                if intent != "submit"
                else "completed" if complete else "incomplete"
            )
            c.execute(
                "UPDATE assessments SET status=?,completed_at=?,seen_at=NULL,revision=revision+1 WHERE id=?",
                (status, now() if intent == "submit" else None, aid),
            )
            if result.get("needs_safety_clarification") and not previously_positive:
                old_event = c.execute(
                    "SELECT id FROM assessment_events WHERE assessment_id=? LIMIT 1",
                    (aid,),
                ).fetchone()
                kind = (
                    "phq9_item9"
                    if not old_event
                    else f'phq9_item9_r{current["revision"]+1}'
                )
                c.execute(
                    "INSERT OR IGNORE INTO assessment_events(patient_id,assessment_id,kind,created_at) VALUES(?,?,?,?)",
                    (a["patient_id"], aid, kind, now()),
                )
            if a["scale"] == "safety6":
                new_yes = [
                    key
                    for key in result["positive_items"]
                    if answered_value(old_safety, key) != 1
                ]
                if new_yes:
                    kind = (
                        "safety6_yes_"
                        + "_".join(new_yes)
                        + f"_r{current['revision']+1}"
                    )
                    c.execute(
                        "INSERT OR IGNORE INTO assessment_events(patient_id,assessment_id,kind,created_at) VALUES(?,?,?,?)",
                        (a["patient_id"], aid, kind, now()),
                    )
            c.execute(
                "INSERT INTO save_requests VALUES(?,?,?)", (token, user["id"], payload)
            )
            audit(c, user["id"], "assessment_saved", a["patient_id"], aid)
        return RedirectResponse(target, status_code=303)

    @app.post("/patients/{pid}/repeat")
    def repeat(request: Request, pid: int, csrf: str = Form(...)):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Повтор назначает специалист")
        csrf_check(request, csrf)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT id FROM assessments WHERE patient_id=? AND scale='phq4' AND status='draft' LIMIT 1",
                (pid,),
            ).fetchone()
            if not existing:
                sid = c.execute(
                    "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
                    (
                        pid,
                        (date.today() - timedelta(days=13)).isoformat(),
                        date.today().isoformat(),
                        now(),
                    ),
                ).lastrowid
                aid = c.execute(
                    "INSERT INTO assessments(session_id,patient_id,scale,version,assigned_by) VALUES(?,?,'phq4',?,?)",
                    (sid, pid, VERSION, account(request)["id"]),
                ).lastrowid
                audit(c, account(request)["id"], "repeat_assigned", pid, aid)
        return RedirectResponse(f"/patients/{pid}#assessments", status_code=303)

    @app.post("/patients/{pid}/assign")
    def assign(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        scale: str = Form(...),
        session_id: int = Form(...),
        due_day: date = Form(...),
    ):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Назначение делает специалист")
        csrf_check(request, csrf)
        if scale not in ["phq9", "gad7", "phqads", "hcl33"] or due_day < date.today():
            raise HTTPException(422, "Проверьте форму и срок")
        with db() as c:
            if not c.execute(
                "SELECT id FROM assessment_sessions WHERE id=? AND patient_id=?",
                (session_id, pid),
            ).fetchone():
                raise HTTPException(422, "Сессия не принадлежит пациенту")
            for assigned_scale in ["phq9", "gad7"] if scale == "phqads" else [scale]:
                c.execute(
                    "INSERT OR IGNORE INTO assessments(session_id,patient_id,scale,version,assigned_by,due_day) VALUES(?,?,?,?,?,?)",
                    (
                        session_id,
                        pid,
                        assigned_scale,
                        VERSION,
                        account(request)["id"],
                        due_day.isoformat(),
                    ),
                )
            audit(c, account(request)["id"], "assessment_assigned", pid, session_id)
        return RedirectResponse(f"/patients/{pid}#assessments", status_code=303)

    @app.post("/assessments/{aid}/review")
    def review(
        request: Request,
        aid: int,
        csrf: str = Form(...),
        intent: str = Form(...),
        item: str = Form(""),
    ):
        a = assessment(request, aid)
        if identity(request) != "doctor":
            raise HTTPException(403, "Только специалист")
        csrf_check(request, csrf)
        with db() as c:
            if intent == "read":
                c.execute("UPDATE assessments SET seen_at=? WHERE id=?", (now(), aid))
            elif intent == "discussed" and item.isdigit():
                c.execute(
                    "UPDATE answers SET discussed=1 WHERE assessment_id=? AND item=? AND discuss=1",
                    (aid, item),
                )
            else:
                raise HTTPException(422, "Неизвестное действие")
            audit(
                c, account(request)["id"], "assessment_" + intent, a["patient_id"], aid
            )
        return RedirectResponse(f"/assessments/{aid}", status_code=303)

    @app.get("/patients/{pid}/results")
    def results(request: Request, pid: int):
        access(request, pid)
        data = overview(db, pid)
        pairs = {}
        for a in data["assessments"]:
            if a["scale"] == "safety6":
                continue
            pairs.setdefault(a["session_id"], {})[a["scale"]] = {
                **a,
                "total": a["result"]["total"],
            }
        summaries = []
        for sid, forms in pairs.items():
            total, reason = combined(forms.get("phq9"), forms.get("gad7"))
            summaries.append({"sid": sid, "total": total, "reason": reason})
        graphs = []
        for scale in ["phq9", "gad7"]:
            rows = sorted(
                [a for a in data["assessments"] if a["scale"] == scale],
                key=lambda a: (a["period_end"], a["id"]),
            )
            segments = []
            current = []
            points = []
            for i, a in enumerate(rows):
                total = a["result"]["total"]
                if total is None:
                    if current:
                        segments.append(" ".join(current))
                        current = []
                    continue
                x = 40 + i * 520 / max(len(rows) - 1, 1)
                y = 165 - total / SCALES[scale]["max"] * 130
                current.append(f"{x:.1f},{y:.1f}")
                points.append({"x": x, "y": y, "assessment": a, "total": total})
            if current:
                segments.append(" ".join(current))
            graphs.append(
                {
                    "scale": scale,
                    "name": SCALES[scale]["name"],
                    "max": SCALES[scale]["max"],
                    "segments": segments,
                    "points": points,
                }
            )
        with db() as c:
            audit(c, account(request)["id"], "results_view", pid)
        return render(
            request,
            "results.html",
            pid=pid,
            data=data,
            summaries=summaries,
            graphs=graphs,
        )

    @app.get("/api/workflow-alerts")
    def workflow_alerts(request: Request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Только специалист")
        with db() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT e.*,p.name FROM assessment_events e JOIN patients p ON p.id=e.patient_id WHERE p.doctor_id=? ORDER BY e.id DESC",
                    (user["doctor_id"],),
                )
            ]
        for e in rows:
            e["awaiting_seconds"] = max(
                0,
                int(
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(e["created_at"])
                    ).total_seconds()
                ),
            )
        return {"events": rows, "reserve_configured": False}

    @app.post("/events/{eid}/status")
    def event_status(
        request: Request,
        eid: int,
        csrf: str = Form(...),
        intent: str = Form(...),
        action_text: str = Form("", max_length=3000),
    ):
        with db() as c:
            row = c.execute(
                "SELECT * FROM assessment_events WHERE id=?", (eid,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Событие не найдено")
        if access(request, row["patient_id"]) != "doctor":
            raise HTTPException(403, "Только специалист")
        csrf_check(request, csrf)
        with db() as c:
            if intent == "delivered":
                c.execute(
                    "UPDATE assessment_events SET delivered_at=COALESCE(delivered_at,?) WHERE id=?",
                    (now(), eid),
                )
            elif intent == "acknowledge":
                c.execute(
                    "UPDATE assessment_events SET delivered_at=COALESCE(delivered_at,?),acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                    (now(), now(), eid),
                )
            elif intent == "action" and action_text.strip():
                if not row["acknowledged_at"]:
                    raise HTTPException(409, "Сначала подтвердите просмотр события")
                c.execute(
                    "UPDATE assessment_events SET action_text=?,action_at=? WHERE id=?",
                    (action_text.strip(), now(), eid),
                )
            else:
                raise HTTPException(422, "Нужно действие и его описание")
            audit(c, account(request)["id"], "event_" + intent, row["patient_id"], eid)
        if intent == "delivered":
            return {"status": "delivered"}
        return RedirectResponse(
            f"/patients/{row['patient_id']}#safety", status_code=303
        )

    @app.get("/patients/{pid}/audit")
    def audit_page(request: Request, pid: int):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Журнал доступен специалисту")
        with db() as c:
            rows = c.execute(
                "SELECT action,object_id,created_at,account_id FROM audit WHERE patient_id=? ORDER BY id DESC LIMIT 100",
                (pid,),
            ).fetchall()
        return render(request, "audit.html", pid=pid, rows=rows)

    @app.post("/assessments/{aid}/audio")
    async def audio_save(
        request: Request,
        aid: int,
        csrf: str = Form(...),
        item: str = Form(...),
        duration: float = Form(..., gt=0, le=60),
        consent: bool = Form(False),
        recording: UploadFile = File(...),
        transcript: str = Form("", max_length=3000),
        transcript_reviewed: bool = Form(False),
    ):
        a = assessment(request, aid, True)
        csrf_check(request, csrf)
        if (
            not consent
            or not item.isdigit()
            or not 1 <= int(item) <= len(SCALES[a["scale"]]["items"])
        ):
            raise HTTPException(422, "Нужны согласие и номер вопроса")
        if a["scale"] == "safety6":
            with db() as c:
                values = {
                    r["item"]: dict(r)
                    for r in c.execute(
                        "SELECT * FROM answers WHERE assessment_id=?", (aid,)
                    )
                }
            if int(item) not in safety_path(values):
                raise HTTPException(409, "Этот вопрос сейчас не предусмотрен")
        content = bytearray()
        while chunk := await recording.read(65536):
            content.extend(chunk)
            if len(content) > 3 * 1024 * 1024:
                raise HTTPException(413, "Запись больше 3 МБ")
        mime = recording.content_type.split(";")[0] if recording.content_type else ""
        valid = (
            (mime == "audio/webm" and content[:4] == b"\x1aE\xdf\xa3")
            or (mime == "audio/ogg" and content[:4] == b"OggS")
            or (mime == "audio/mp4" and content[4:8] == b"ftyp")
        )
        if not valid:
            raise HTTPException(422, "Нужна аудиозапись WebM, Ogg или MP4")
        if transcript and not transcript_reviewed:
            raise HTTPException(422, "Подтвердите проверку расшифровки")
        with db() as c:
            c.execute(
                "INSERT INTO audio_notes(patient_id,assessment_id,item,file_name,mime,duration,consent_at,transcript,transcript_status,created_at,content) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    a["patient_id"],
                    aid,
                    item,
                    secrets.token_hex(16),
                    mime,
                    duration,
                    now(),
                    transcript,
                    "reviewed_patient" if transcript else "not_transcribed",
                    now(),
                    bytes(content),
                ),
            )
            c.execute("UPDATE assessments SET seen_at=NULL WHERE id=?", (aid,))
            audit(c, account(request)["id"], "audio_saved", a["patient_id"], aid)
        return RedirectResponse(
            f"/assessments/{aid}?item={int(item)}&audio_saved=1", status_code=303
        )

    @app.get("/audio/{audio_id}")
    def audio_get(request: Request, audio_id: int):
        with db() as c:
            row = c.execute(
                "SELECT * FROM audio_notes WHERE id=? AND deleted_at IS NULL",
                (audio_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Запись не найдена")
        access(request, row["patient_id"])
        with db() as c:
            audit(c, account(request)["id"], "audio_play", row["patient_id"], audio_id)
        return Response(
            content=row["content"],
            media_type=row["mime"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/audio/{audio_id}/delete")
    def audio_delete(request: Request, audio_id: int, csrf: str = Form(...)):
        with db() as c:
            row = c.execute(
                "SELECT * FROM audio_notes WHERE id=? AND deleted_at IS NULL",
                (audio_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Запись не найдена")
        access(request, row["patient_id"], True)
        csrf_check(request, csrf)
        with db() as c:
            c.execute(
                "UPDATE audio_notes SET deleted_at=?,content=NULL WHERE id=?",
                (now(), audio_id),
            )
            audit(
                c, account(request)["id"], "audio_deleted", row["patient_id"], audio_id
            )
        return RedirectResponse(
            f"/assessments/{row['assessment_id']}?item={int(row['item'])}",
            status_code=303,
        )
