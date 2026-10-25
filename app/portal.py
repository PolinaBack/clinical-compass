"""Patient history, diary notes, reports and consultation scheduling."""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import Request, Form, HTTPException
from fastapi.responses import RedirectResponse

from .storage import now, audit
from .scales import SCALES, OPTIONS
from .charts import chart

PROFILE_FIELDS = (
    ("biography", "Обо мне", "Учёба, работа, интересы и то, что важно знать обо мне."),
    ("goals", "Что я хочу изменить", "Мои цели и ожидания от поддержки."),
    (
        "course",
        "История моего состояния",
        "Когда началось, что изменилось в жизни, были ли похожие периоды и помощь раньше.",
    ),
    (
        "health",
        "Здоровье и сон",
        "Заболевания, текущие назначения, сон и другие сведения, которые хочу обсудить.",
    ),
    (
        "stressors",
        "События жизни · по желанию",
        "Можно не раскрывать подробности и обсудить лично.",
    ),
    (
        "family_context",
        "Семейная история",
        "Что известно, от кого и насколько уверенно. Можно написать «не знаю» или отложить.",
    ),
)


def portal_data(db, pid, doctor=False):
    with db() as c:
        profile = c.execute(
            "SELECT * FROM patient_profiles WHERE patient_id=?", (pid,)
        ).fetchone()
        comments = [
            dict(r)
            for r in c.execute(
                "SELECT dc.*,d.name author FROM diary_comments dc JOIN accounts a ON a.id=dc.author_id JOIN doctors d ON d.id=a.doctor_id WHERE dc.patient_id=? ORDER BY dc.id",
                (pid,),
            )
        ]
        notes = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM diary_notes WHERE patient_id=? ORDER BY id DESC", (pid,)
            )
        ]
        bookings = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM consultation_requests WHERE patient_id=? ORDER BY id DESC",
                (pid,),
            )
        ]
        visits = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM appointments WHERE patient_id=? ORDER BY starts_at DESC,id DESC",
                (pid,),
            )
        ]
        safety = c.execute(
            "SELECT * FROM safety_reviews WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        topics = []
        if doctor:
            for row in c.execute(
                "SELECT a.id assessment_id,a.scale,an.item,an.value,an.state,an.comment,an.discuss FROM answers an JOIN assessments a ON a.id=an.assessment_id WHERE a.patient_id=? AND (an.discuss=1 OR an.state='declined' OR (a.scale='safety6' AND an.value=1) OR (a.scale='phq9' AND an.item='9' AND an.value>0)) ORDER BY a.id DESC,an.item LIMIT 12",
                (pid,),
            ):
                if not row["item"].isdigit():
                    continue
                question = SCALES[row["scale"]]["items"][int(row["item"]) - 1]
                positive = (
                    row["scale"] == "phq9"
                    and row["item"] == "9"
                    and row["state"] == "answered"
                    and row["value"] > 0
                )
                topics.append(
                    dict(row)
                    | {
                        "question": question,
                        "answer_label": (
                            ("Да" if row["value"] else "Нет")
                            if row["state"] == "answered"
                            and row["scale"] in ["hcl33", "safety6"]
                            else (
                                OPTIONS[row["value"]][1]
                                if row["state"] == "answered"
                                else (
                                    "Отказ от ответа"
                                    if row["state"] == "declined"
                                    else "Ответ не завершён"
                                )
                            )
                        ),
                        "prompt": (
                            "Спасибо, что рассказали. Вы отметили мысли о смерти или причинении себе боли. Можете рассказать, что происходит с вами сейчас?"
                            if positive
                            else (
                                "Спасибо, что рассказали. Что вам важно обсудить об этом переживании сейчас?"
                                if row["scale"] == "safety6"
                                and row["state"] == "answered"
                                and row["value"] == 1
                                else (
                                    "Можно не рассказывать всё сразу. Что помогло бы вам обсудить этот вопрос со мной?"
                                    if row["state"] == "declined"
                                    else "Вы отметили этот вопрос для беседы. С чего вам было бы удобнее начать?"
                                )
                            )
                        ),
                    }
                )
    return dict(
        conversation_topics=topics,
        profile=dict(profile) if profile else {},
        profile_fields=PROFILE_FIELDS,
        diary_comments=comments,
        diary_notes=notes,
        bookings=bookings,
        appointments=visits,
        safety_review=dict(safety) if safety else None,
    )


def register_portal(
    app, db, account, identity, access, csrf_check, render, patient_context
):
    def specialist(request, pid):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Это действие доступно назначенному специалисту")
        return account(request)

    def redirect(pid, section=""):
        return RedirectResponse(f"/patients/{pid}{section}", status_code=303)

    @app.get("/me/start")
    def introduction(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Раздел пациента")
        return render(
            request,
            "home.html",
            patient_home=True,
            **patient_context(request, user["patient_id"]),
        )

    @app.get("/patients/{pid}/profile")
    def profile_page(request: Request, pid: int):
        access(request, pid)
        if account(request)["role"] == "doctor":
            return profile_section(request, pid, "about")
        return RedirectResponse(f"/patients/{pid}/profile/about", status_code=303)

    @app.get("/patients/{pid}/profile/{section}")
    def profile_section(request: Request, pid: int, section: str, view: str = "tree"):
        if section not in ["about", "assessments", "family", "video", "assignments"]:
            raise HTTPException(404, "Раздел не найден")
        context = patient_context(request, pid)
        if account(request)["role"] == "doctor" and section in (
            "video",
            "assessments",
            "assignments",
        ):
            return RedirectResponse(
                (
                    f"/doctor/calendar?patient_id={pid}#meeting-workspace"
                    if section == "video"
                    else (
                        f"/patients/{pid}/assignments"
                        if section == "assignments"
                        else f"/patients/{pid}/workspace/assessments"
                    )
                ),
                status_code=303,
            )
        if section == "assignments":
            from .routes.care_orders import context as care_context

            with db() as c:
                context.update(
                    care_context(c, app.state.db_path, account(request), pid)
                )
        if section == "video":
            from .services.consultations import profile_context

            with db() as c:
                context.update(profile_context(c, account(request), pid))
        if section == "about":
            from .routes.intake import intake_context

            with db() as c:
                context.update(
                    intake_context(
                        c,
                        account(request),
                        pid,
                        request.query_params.get("view", "summary"),
                        (
                            int(request.query_params["submission_id"])
                            if request.query_params.get("submission_id", "").isdigit()
                            else None
                        ),
                        request.query_params.get("as_of"),
                    )
                )
        if section == "family":
            from .family import family_tree
            from .routes.intake import intake_context

            context.update(
                family_tree(
                    context["relatives"], request.query_params.get("condition", "")
                ),
                family_view="list" if view == "list" else "tree",
                family_condition_labels=sorted(
                    {
                        c["label"]
                        for r in context["relatives"]
                        for c in r.get("conditions", [])
                    }
                ),
            )
            with db() as c:
                # Earlier intake family answers stay private or submitted as before.
                context.update(intake_context(c, account(request), pid))
            for node in context["family_nodes"]:
                if node["key"] == "self":
                    node["age"] = context["intake_calculations"]["age"]
                    value = (
                        context["intake_data"]
                        .get("answers", {})
                        .get("personal", {})
                        .get("sex", {})
                    )
                    node["sex"] = {"Мужской": "male", "Женский": "female"}.get(
                        value.get("value"), "unknown"
                    )
        if section == "family":
            for node in context["family_patient_nodes"]:
                if node["key"] == "self":
                    node["age"] = context["intake_calculations"]["age"]
        template = "profile.html"
        if account(request)["role"] == "doctor" and section == "about":
            from .services.clinician_profile import build

            if not request.query_params.get("as_of"):
                from .services.intake import computations

                context["intake_calculations"] = computations(context["intake_data"])

            context["clinical_profile"] = build(context)
            template = "clinician_profile.html"
        response = render(request, template, section=section, **context)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/patients/{pid}/diary")
    def diary_page(request: Request, pid: int):
        context = patient_context(request, pid)
        return render(
            request,
            "patient.html",
            diary_graph=chart(
                context["diary"],
                10,
                "Самочувствие по дневнику",
                "diary",
                "day",
                "wellbeing",
            ),
            **context,
        )

    @app.post("/patients/{pid}/notes")
    def note(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        body: str = Form(..., min_length=1, max_length=3000),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        if not body.strip():
            raise HTTPException(422, "Напишите заметку")
        with db() as c:
            nid = c.execute(
                "INSERT INTO diary_notes(patient_id,body,created_at) VALUES(?,?,?)",
                (pid, body.strip(), now()),
            ).lastrowid
            audit(c, account(request)["id"], "diary_note_saved", pid, nid)
        return redirect(pid, "/diary#notes")

    @app.post("/patients/{pid}/consultations")
    def request_visit(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        slot_id: int = Form(..., gt=0, le=2**63 - 1),
        url: str = Form("", max_length=2000),
        fallback: str = Form("", max_length=1000),
    ):
        user = account(request)
        access(request, pid, user["role"] == "patient")
        csrf_check(request, csrf)
        from .services.consultations import book

        if user["role"] != "doctor" and (url or fallback):
            raise HTTPException(403, "Ссылку на видеоприём задаёт врач")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            appointment_id = book(
                c,
                user,
                pid,
                slot_id,
                connection=(url.strip(), fallback.strip()) if url or fallback else None,
            )
            booked = c.execute(
                "SELECT starts_at FROM appointments WHERE id=?", (appointment_id,)
            ).fetchone()
        if user["role"] == "doctor":
            from .services.doctor_calendar import location

            return RedirectResponse(
                location(pid, booked["starts_at"][:10], aid=appointment_id),
                status_code=303,
            )
        return redirect(pid, "/profile/video#video")

    @app.post("/patients/{pid}/profile")
    async def profile_save(request: Request, pid: int):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        # Hidden legacy fields remain stored when the current form omits them.
        with db() as c:
            previous = c.execute(
                "SELECT * FROM patient_profiles WHERE patient_id=?", (pid,)
            ).fetchone()
        values = [
            str(form.get(key, previous[key] if previous else "")).strip()
            for key, _, _ in PROFILE_FIELDS
        ]
        if any(len(value) > 3000 for value in values):
            raise HTTPException(422, "Каждое поле — до 3000 символов")
        with db() as c:
            c.execute(
                "INSERT INTO patient_profiles VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET biography=excluded.biography,goals=excluded.goals,course=excluded.course,health=excluded.health,stressors=excluded.stressors,family_context=excluded.family_context,updated_at=excluded.updated_at",
                (pid, *values, now()),
            )
            audit(c, account(request)["id"], "profile_saved", pid)
        return redirect(pid, "/profile/about?saved=1")

    @app.post("/patients/{pid}/diary/{did}/comments")
    def diary_comment(
        request: Request,
        pid: int,
        did: int,
        csrf: str = Form(...),
        body: str = Form(..., min_length=1, max_length=3000),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        if not body.strip():
            raise HTTPException(422, "Напишите комментарий")
        with db() as c:
            if not c.execute(
                "SELECT id FROM diary WHERE id=? AND patient_id=?", (did, pid)
            ).fetchone():
                raise HTTPException(404, "Запись дневника не найдена")
            cid = c.execute(
                "INSERT INTO diary_comments(patient_id,diary_id,author_id,body,created_at) VALUES(?,?,?,?,?)",
                (pid, did, user["id"], body.strip(), now()),
            ).lastrowid
            audit(c, user["id"], "diary_comment_added", pid, cid)
        return redirect(pid, f"/diary#diary-entry-{did}")

    @app.post("/patients/{pid}/reports")
    @app.post("/patients/{pid}/reports/{rid}/publish")
    async def retired_reports(request: Request, pid: int, rid: int = 0):
        specialist(request, pid)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        raise HTTPException(410, "Создание и публикация отчётов отключены.")

    @app.get("/patients/{pid}/contact")
    def old_contact(request: Request, pid: int):
        access(request, pid)
        return redirect(pid, "/profile/video")

    @app.post("/patients/{pid}/questions")
    @app.post("/patients/{pid}/questions/{qid}/reply")
    def old_questions(request: Request, pid: int, qid: int | None = None):
        access(request, pid)
        raise HTTPException(410, "Личные вопросы закрыты. Запишитесь на консультацию.")

    @app.post("/patients/{pid}/appointments")
    def appointment(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        starts_at: str = Form(..., max_length=30),
        timezone_label: str = Form(..., min_length=1, max_length=80),
        url: str = Form(..., max_length=2000),
        fallback: str = Form(..., min_length=1, max_length=1000),
        booking_id: str | None = Form(None, max_length=20),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        try:
            dt = datetime.fromisoformat(starts_at)
            if booking_id in (None, ""):
                booking_id = None
            else:
                booking_id = int(booking_id)
                if not 0 < booking_id < 2**63:
                    raise ValueError()
            parsed = urlsplit(url.strip())
            valid_url = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not any(char.isspace() or ord(char) < 32 for char in url)
            )
        except ValueError:
            raise HTTPException(422, "Проверьте время и ссылку")
        if not valid_url or not timezone_label.strip() or not fallback.strip():
            raise HTTPException(
                422, "Нужны HTTPS-ссылка, часовой пояс и способ связи при обрыве"
            )
        if dt.tzinfo is not None:
            raise HTTPException(
                422, "Укажите местное время и его часовой пояс отдельно"
            )
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            from .services import consultations

            start = consultations.local_start(dt.isoformat(), timezone_label.strip())
            if start > consultations.utc_now():
                # Legacy API remains usable, but cannot overwrite published availability.
                day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                if c.execute(
                    "SELECT 1 FROM consultation_slots WHERE doctor_id=? AND status IN ('open','booked') AND starts_at<? AND ends_at>?",
                    (
                        user["doctor_id"],
                        consultations.stamp(day_start + timedelta(days=1)),
                        consultations.stamp(day_start),
                    ),
                ).fetchone():
                    raise HTTPException(
                        409,
                        "Выберите опубликованное окно; у прежнего формата приёма не задана длительность",
                    )
                consultations.conflict(
                    c, user["doctor_id"], start, start + timedelta(minutes=1)
                )
            if (
                booking_id is not None
                and not c.execute(
                    "SELECT id FROM consultation_requests WHERE id=? AND patient_id=? AND status='requested'",
                    (booking_id, pid),
                ).fetchone()
            ):
                raise HTTPException(
                    422, "Заявка не принадлежит пациенту или уже обработана"
                )
            vid = c.execute(
                "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    pid,
                    user["id"],
                    dt.isoformat(timespec="minutes"),
                    timezone_label.strip(),
                    url.strip(),
                    fallback.strip(),
                    now(),
                ),
            ).lastrowid
            if booking_id is not None:
                c.execute(
                    "UPDATE consultation_requests SET status='confirmed',appointment_id=? WHERE id=?",
                    (vid, booking_id),
                )
            audit(c, user["id"], "appointment_planned", pid, vid)
        from .services.doctor_calendar import location

        return RedirectResponse(
            location(pid, dt.date().isoformat(), aid=vid), status_code=303
        )

    @app.post("/patients/{pid}/appointments/{vid}/status")
    def visit_status(
        request: Request,
        pid: int,
        vid: int,
        csrf: str = Form(...),
        intent: str = Form(...),
        identity_checked: bool = Form(False),
        location_checked: bool = Form(False),
        privacy_checked: bool = Form(False),
        fallback_checked: bool = Form(False),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        if intent not in ["ready", "completed", "cancelled"]:
            raise HTTPException(422, "Неизвестное действие")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            visit = c.execute(
                "SELECT * FROM appointments WHERE id=? AND patient_id=?", (vid, pid)
            ).fetchone()
            if not visit:
                raise HTTPException(404, "Приём не найден")
            if visit["status"] in ["completed", "cancelled"]:
                raise HTTPException(409, "Этот приём уже закрыт")
            if intent == "ready" and not all(
                [identity_checked, location_checked, privacy_checked, fallback_checked]
            ):
                raise HTTPException(422, "Подтвердите подготовку к звонку")
            if intent == "completed" and not visit["preparation_confirmed_at"]:
                raise HTTPException(409, "Сначала подтвердите подготовку к приёму")
            c.execute(
                "UPDATE appointments SET status=?,preparation_confirmed_at=CASE WHEN ?='ready' THEN ? ELSE preparation_confirmed_at END WHERE id=?",
                (intent, intent, now(), vid),
            )
            if intent == "cancelled":
                from .services.consultations import release

                release(c, user, visit)
            audit(c, user["id"], "appointment_" + intent, pid, vid)
        from .services.doctor_calendar import location

        return RedirectResponse(
            location(pid, visit["starts_at"][:10], aid=vid), status_code=303
        )

    @app.get("/patients/{pid}/safety")
    def safety_page(request: Request, pid: int):
        return render(request, "safety.html", **patient_context(request, pid))

    @app.post("/patients/{pid}/safety-review")
    def safety_review(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        form_version: str = Form(..., min_length=1, max_length=200),
        source: str = Form(..., min_length=1, max_length=1000),
        notes: str = Form(..., min_length=1, max_length=3000),
        patient_summary: str = Form(..., min_length=1, max_length=3000),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        if not all(
            value.strip() for value in [form_version, source, notes, patient_summary]
        ):
            raise HTTPException(422, "Укажите форму, источник и результаты беседы")
        with db() as c:
            sid = c.execute(
                "INSERT INTO safety_reviews(patient_id,author_id,form_version,source,notes,patient_summary,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    pid,
                    user["id"],
                    form_version.strip(),
                    source.strip(),
                    notes.strip(),
                    patient_summary.strip(),
                    now(),
                ),
            ).lastrowid
            audit(c, user["id"], "external_safety_review_recorded", pid, sid)
        return redirect(pid, "/safety")
