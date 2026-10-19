"""Patient history, diary notes, reports and consultation scheduling."""

from datetime import datetime
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
        reports = [
            dict(r)
            for r in c.execute(
                "SELECT r.*,d.name author FROM specialist_reports r JOIN accounts a ON a.id=r.author_id JOIN doctors d ON d.id=a.doctor_id WHERE r.patient_id=? AND (r.published=1 OR ?=1) ORDER BY r.id DESC",
                (pid, int(doctor)),
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
        reports=reports,
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
        return RedirectResponse(f"/patients/{pid}/profile/about", status_code=303)

    @app.get("/patients/{pid}/profile/{section}")
    def profile_section(request: Request, pid: int, section: str, view: str = "tree"):
        if section not in ["about", "assessments", "family", "video"]:
            raise HTTPException(404, "Раздел не найден")
        context = patient_context(request, pid)
        if section == "family":
            from .family import family_tree

            context.update(
                family_tree(context["relatives"]),
                family_view="list" if view == "list" else "tree",
            )
        return render(request, "profile.html", section=section, **context)

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
        preferred_day: str = Form(...),
        time_window: str = Form(..., min_length=1, max_length=100),
        timezone_label: str = Form(..., min_length=1, max_length=80),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        from datetime import date

        try:
            day = date.fromisoformat(preferred_day)
        except ValueError:
            raise HTTPException(422, "Проверьте дату")
        if day < date.today() or not time_window.strip() or not timezone_label.strip():
            raise HTTPException(422, "Выберите будущую дату, время и часовой пояс")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT id FROM consultation_requests WHERE patient_id=? AND preferred_day=? AND time_window=? AND timezone_label=? AND status='requested'",
                (pid, day.isoformat(), time_window.strip(), timezone_label.strip()),
            ).fetchone()
            if existing:
                return redirect(pid, "/profile/video#booking")
            c.execute(
                "INSERT INTO consultation_requests(patient_id,preferred_day,time_window,timezone_label,created_at) VALUES(?,?,?,?,?)",
                (
                    pid,
                    day.isoformat(),
                    time_window.strip(),
                    timezone_label.strip(),
                    now(),
                ),
            )
            audit(c, account(request)["id"], "consultation_requested", pid)
        return redirect(pid, "/profile/video#booking")

    @app.post("/patients/{pid}/profile")
    async def profile_save(request: Request, pid: int):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        values = [str(form.get(key, "")).strip() for key, _, _ in PROFILE_FIELDS]
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
    def report(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        title: str = Form(..., min_length=1, max_length=120),
        self_report: str = Form("", max_length=3000),
        observations: str = Form("", max_length=3000),
        conclusion: str = Form(..., min_length=1, max_length=3000),
        plan: str = Form(..., min_length=1, max_length=3000),
        published: bool = Form(False),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        if not all(value.strip() for value in [title, conclusion, plan]):
            raise HTTPException(422, "Заполните название, заключение и план")
        with db() as c:
            rid = c.execute(
                "INSERT INTO specialist_reports(patient_id,author_id,title,self_report,observations,conclusion,plan,published,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    user["id"],
                    title.strip(),
                    self_report.strip(),
                    observations.strip(),
                    conclusion.strip(),
                    plan.strip(),
                    int(published),
                    now(),
                ),
            ).lastrowid
            audit(
                c,
                user["id"],
                "report_published" if published else "report_drafted",
                pid,
                rid,
            )
        return redirect(pid, "/results#reports")

    @app.post("/patients/{pid}/reports/{rid}/publish")
    def publish_report(request: Request, pid: int, rid: int, csrf: str = Form(...)):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        with db() as c:
            result = c.execute(
                "UPDATE specialist_reports SET published=1 WHERE id=? AND patient_id=? AND published=0",
                (rid, pid),
            )
            if not result.rowcount:
                raise HTTPException(404, "Черновик не найден")
            audit(c, user["id"], "report_published", pid, rid)
        return redirect(pid, "/results#reports")

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
        booking_id: int | None = Form(None),
    ):
        user = specialist(request, pid)
        csrf_check(request, csrf)
        try:
            dt = datetime.fromisoformat(starts_at)
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
        return redirect(pid, "/profile/video#video")

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
            audit(c, user["id"], "appointment_" + intent, pid, vid)
        return redirect(pid, "/profile/video#video")

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
