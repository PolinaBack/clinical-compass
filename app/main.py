"""Local synthetic demonstration. No diagnostic or medication rules."""

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from .content import FAQ, RESOURCES
from .assets import asset_url
from .security import verify_password
from .session_keys import session_secret
from .storage import extend_schema, audit, now
from .workflows import register_workflows, overview
from .migrations import apply_migrations
from .routes.settings import register_settings
from .routes.interface import register_interface
from .services.interface import read_interface
from .services.diary import read_diary_extras, sleep_rating
from .routes.diary import register_diary
from .routes.diary_library import register_diary_library
from .routes.intake import register_intake
from .services import diary_questions as questions
from .services import clinical_attachments as attachment_files
from .portal import register_portal, portal_data
from .community import register_community, public_forum_data
from .family import register_family
from .routes.drug_library import register_drug_library
from .routes.doctor_medicines import register_doctor_medicines
from .routes.medicine_drafts import register_medicine_drafts
from .routes.care_orders import register_care_orders
from .routes.medicine_reactions import register_medicine_reactions
from .services.drug_library import bootstrap as bootstrap_drug_library
from .routes.meetings import register_meetings
from .routes.experimental_ai import register_experimental_ai
from .routes.patient_ai import register_patient_ai
from .routes.clinician_forum import register_clinician_forum
from .recordings import register_recordings

ROOT = Path(__file__).resolve().parent


def create_app(db_path=None):
    path = Path(
        db_path or os.getenv("COMPASS_DB", str(ROOT.parent / "data" / "compass.db"))
    )

    @contextmanager
    def db():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    @asynccontextmanager
    async def lifespan(app):
        path.parent.mkdir(parents=True, exist_ok=True)
        with db() as c:
            c.executescript(
                """
            CREATE TABLE IF NOT EXISTS doctors(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT OR IGNORE INTO doctors VALUES(1, 'Демо-врач');
            CREATE TABLE IF NOT EXISTS patients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, doctor_id INTEGER NOT NULL DEFAULT 1 REFERENCES doctors(id));
            CREATE TABLE IF NOT EXISTS diary(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), day TEXT NOT NULL, wellbeing INTEGER NOT NULL CHECK(wellbeing BETWEEN 0 AND 10), sleep REAL NOT NULL CHECK(sleep BETWEEN 0 AND 24), adherence TEXT NOT NULL, note TEXT NOT NULL, contact INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS screenings(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), day TEXT NOT NULL, tension INTEGER NOT NULL, difficulty INTEGER NOT NULL, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), author TEXT NOT NULL, body TEXT NOT NULL, day TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS risks(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), day TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, urgent INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), day TEXT NOT NULL, category TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL, confirmed INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS acknowledgements(patient_id INTEGER PRIMARY KEY REFERENCES patients(id), risk_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS relatives(id INTEGER PRIMARY KEY, patient_id INTEGER REFERENCES patients(id), name TEXT NOT NULL, relation TEXT NOT NULL, parent_id INTEGER REFERENCES relatives(id));
            INSERT OR IGNORE INTO patients(id,name,doctor_id) VALUES(1, 'Демо-пациент А',1), (2, 'Демо-пациент Б',1);
            """
            )
            extend_schema(c)
        with db() as c:
            apply_migrations(c)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            bootstrap_drug_library(c)
            from .services.materials import bootstrap

            bootstrap(c)
            from .services.assessment_followups import ensure_followups

            ensure_followups(c)
        yield

    app = FastAPI(title="Клинический компас — демо", lifespan=lifespan)
    from .security import hash_password

    app.state.dummy_hash = hash_password(secrets.token_urlsafe(24))
    app.state.db_path = path
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(path),
        same_site="strict",
        max_age=28800,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    from .services.clinical_forms import local_datetime

    templates.env.filters["local_datetime"] = local_datetime
    from .services.ai_display import readable_text

    templates.env.filters["ai_readable_text"] = readable_text
    from .services.diary_library import palette, schedule_label, CATEGORIES
    from .services.diary_questions import FREQUENCIES

    templates.env.globals.update(
        diary_palette=palette,
        diary_schedule=schedule_label,
        diary_colors=CATEGORIES,
        diary_frequencies=FREQUENCIES,
    )
    templates.env.globals["asset_url"] = asset_url

    def account(request):
        uid = request.session.get("account_id")
        with db() as c:
            row = c.execute(
                "SELECT * FROM accounts WHERE id=? AND active=1", (uid,)
            ).fetchone()
        if not row or row["auth_version"] != request.session.get("auth_version"):
            raise HTTPException(401, "Сессия завершена. Войдите в свой кабинет.")
        return row

    def identity(request):
        user = account(request)
        return "doctor" if user["role"] == "doctor" else f"patient{user['patient_id']}"

    def has_consent(pid):
        with db() as c:
            row = c.execute(
                "SELECT accepted,adult FROM consents WHERE patient_id=? ORDER BY id DESC LIMIT 1",
                (pid,),
            ).fetchone()
        return bool(row and row["accepted"] and row["adult"])

    def access(request, pid, write_patient=False):
        user = account(request)
        with db() as c:
            p = c.execute(
                "SELECT id,doctor_id FROM patients WHERE id=?", (pid,)
            ).fetchone()
            allowed = p and (
                (user["role"] == "doctor" and p["doctor_id"] == user["doctor_id"])
                or (user["role"] == "patient" and user["patient_id"] == pid)
            )
            if not allowed or (write_patient and user["role"] == "doctor"):
                audit(c, user["id"], "access_denied", pid)
        if not allowed or (write_patient and user["role"] == "doctor"):
            raise HTTPException(403 if p else 404, "Нет доступа к этой карточке")
        if write_patient and not has_consent(pid):
            raise HTTPException(
                403,
                "Сначала ознакомьтесь с правилами и подтвердите участие в синтетической демонстрации.",
            )
        return "doctor" if user["role"] == "doctor" else f"patient{pid}"

    def csrf_check(request, token):
        if (
            not secrets.compare_digest(request.session.get("csrf", ""), token)
            or not token
        ):
            raise HTTPException(403, "Недействительный CSRF-токен. Обновите страницу.")

    def render(request, template, **context):
        request.session.setdefault("csrf", secrets.token_urlsafe(24))
        if request.method == "GET" and template != "error.html":
            from .services.navigation import local_destination

            location = request.url.path + (
                "?" + request.url.query if request.url.query else ""
            )
            if len(location) <= 1024 and local_destination(request, location):
                request.session["last_html_page"] = location
        interface_prefs = dict(minimal=False, revision=0, cards=[])
        if request.session.get("account_id"):
            with db() as c:
                active = c.execute(
                    "SELECT id FROM accounts WHERE id=? AND active=1 AND auth_version=?",
                    (
                        request.session["account_id"],
                        request.session.get("auth_version"),
                    ),
                ).fetchone()
                if active:
                    interface_prefs = read_interface(c, active["id"])
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={
                "user": request.session.get("user"),
                "csrf": request.session["csrf"],
                "session_pid": request.session.get("patient_id"),
                "resources": RESOURCES,
                "faq_items": FAQ,
                "current_page": request.url.path,
                "interface_prefs": interface_prefs,
                **context,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"detail": str(exc.detail)}, status_code=exc.status_code
            )
        from .services.navigation import error_destination

        response = render(
            request,
            "error.html",
            status=exc.status_code,
            detail=str(exc.detail),
            error_return_url=await error_destination(request),
        )
        response.status_code = exc.status_code
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        from .services.navigation import error_destination

        response = render(
            request,
            "error.html",
            status=422,
            detail="Проверьте обязательные поля, форматы и допустимые значения. Данные не сохранены.",
            error_return_url=await error_destination(request),
        )
        response.status_code = 422
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        from .services.navigation import error_destination
        from fastapi.responses import JSONResponse

        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Действие не выполнено. Попробуйте ещё раз."},
                status_code=500,
            )
        response = render(
            request,
            "error.html",
            status=500,
            detail="Действие не выполнено. Попробуйте ещё раз.",
            error_return_url=await error_destination(request),
        )
        response.status_code = 500
        response.headers["Cache-Control"] = "no-store"
        return response

    def back(pid):
        return RedirectResponse(f"/patients/{pid}", status_code=303)

    @app.get("/")
    def home(request: Request):
        return render(request, "home.html", **public_forum_data(db))

    @app.get("/login/{portal}")
    def login_page(request: Request, portal: str):
        if portal not in ["patient", "specialist"]:
            raise HTTPException(404, "Страница не найдена")
        if request.session.get("account_id"):
            return render(request, "switch.html", portal=portal)
        return render(request, "login.html", portal=portal, error=None)

    @app.post("/login")
    def login(
        request: Request,
        username: str = Form(..., max_length=80),
        password: str = Form(..., max_length=256),
        portal: str = Form(...),
        csrf: str = Form(...),
    ):
        csrf_check(request, csrf)
        if request.session.get("account_id"):
            raise HTTPException(409, "Сначала выйдите из текущего кабинета")
        expected = (
            "doctor"
            if portal == "specialist"
            else "patient" if portal == "patient" else None
        )
        with db() as c:
            user = c.execute(
                "SELECT * FROM accounts WHERE username=? AND active=1",
                (username.strip(),),
            ).fetchone()
            valid = (
                verify_password(password, user["password_hash"])
                if user
                else verify_password(password, app.state.dummy_hash)
            )
            if not user or not valid or user["role"] != expected:
                audit(c, None, "login_failed")
                response = render(
                    request,
                    "login.html",
                    portal=portal,
                    error="Неверные данные или другой тип кабинета",
                    user=None,
                )
                response.status_code = 401
                return response
            audit(c, user["id"], "login")
        request.session.clear()
        role = "doctor" if user["role"] == "doctor" else f"patient{user['patient_id']}"
        request.session.update(
            account_id=user["id"],
            auth_version=user["auth_version"],
            user=role,
            patient_id=user["patient_id"],
            csrf=secrets.token_urlsafe(24),
        )
        return RedirectResponse(
            "/doctor" if role == "doctor" else "/me", status_code=303
        )

    @app.get("/me")
    def me(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Эта страница только для пациента")
        return RedirectResponse(
            f"/patients/{user['patient_id']}/diary", status_code=303
        )

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        # A cached logout form may outlive its cookie. Closing an already closed
        # session is harmless; an authenticated session still requires its token.
        if request.session.get("account_id"):
            csrf_check(request, csrf)
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/doctor")
    def doctor(request: Request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только для врача")
        with db() as c:
            from .services.patient_activity import patients_with_activity

            patients = patients_with_activity(c, account(request)["doctor_id"])
        response = render(request, "doctor.html", patients=patients)
        response.headers["Cache-Control"] = "no-store"
        return response

    def patient_context(request, pid):
        access(request, pid)
        with db() as c:
            p = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
            audit(c, account(request)["id"], "patient_view", pid)
            data = {
                table: [
                    dict(r)
                    for r in c.execute(
                        f"SELECT * FROM {table} WHERE patient_id=? ORDER BY id", (pid,)
                    )
                ]
                for table in [
                    "diary",
                    "screenings",
                    "messages",
                    "relatives",
                    "risks",
                    "decisions",
                ]
            }
        with db() as c:
            for relative in data["relatives"]:
                from .services.family_layout import (
                    name as family_display_name,
                    RELATIONS,
                )

                relative["display_name"] = family_display_name(
                    relative, RELATIONS.get(relative["relation"], "Близкий")
                )
                details = c.execute(
                    "SELECT * FROM family_details WHERE relative_id=?",
                    (relative["id"],),
                ).fetchone()
                if details:
                    relative.update(dict(details))
                meta = c.execute(
                    "SELECT * FROM family_person_meta WHERE relative_id=?",
                    (relative["id"],),
                ).fetchone()
                if meta:
                    relative.update(dict(meta))
                relative["conditions"] = [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM family_conditions WHERE relative_id=? ORDER BY id",
                        (relative["id"],),
                    )
                ]
                relative["condition_count"] = len(relative["conditions"])
        ordered = sorted(data["diary"], key=lambda r: (r["day"], r["id"]))
        points = " ".join(
            f'{30+i*540/max(len(ordered)-1,1):.1f},{170-r["wellbeing"]*14}'
            for i, r in enumerate(ordered)
        )
        with db() as c:
            extras = read_diary_extras(c, pid)
            from .services.information_requests import listing

            information_requests = listing(c, account(request), pid)
        return dict(
            patient=p,
            today=date.today().isoformat(),
            points=points,
            consent=has_consent(pid),
            assessment_data=overview(db, pid),
            information_requests=information_requests,
            latest_diary=max(
                data["diary"], key=lambda r: (r["day"], r["id"]), default=None
            ),
            **extras,
            **portal_data(db, pid, identity(request) == "doctor"),
            **data,
        )

    @app.get("/patients/{pid}")
    def patient(request: Request, pid: int):
        role = access(request, pid)
        if role != "doctor":
            return RedirectResponse(f"/patients/{pid}/diary", status_code=303)
        return RedirectResponse(f"/patients/{pid}/profile", status_code=303)

    @app.get("/patients/{pid}/workspace/{section}")
    def specialist_section(request: Request, pid: int, section: str):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Раздел специалиста")
        if section == "assignments":
            return RedirectResponse(f"/patients/{pid}/assignments", status_code=303)
        if section == "care":
            raise HTTPException(410, "Раздел рисков и решений удалён из кабинета")
        if section not in ["safety", "assessments", "conversation"]:
            raise HTTPException(404, "Раздел не найден")
        if section == "safety":
            return RedirectResponse(
                f"/patients/{pid}/workspace/assessments",
                status_code=303,
            )
        context = patient_context(request, pid)
        if section == "assessments":
            from .services.assessment_results import results_context

            context.update(results_context(context["assessment_data"]))
        return render(
            request,
            "specialist_patient.html",
            section=section,
            **context,
        )

    register_community(app, db, identity, account, csrf_check, render)
    register_clinician_forum(app, db, account, csrf_check, render)
    register_interface(app, db, account, render, patient_context)
    register_care_orders(app, db, account, access, csrf_check, render, patient_context)
    register_medicine_reactions(app, db, account, access, csrf_check)
    from .routes.materials import register_materials

    register_materials(app, db, account, csrf_check, render)
    from .routes.consultations import register_consultations

    register_consultations(app, db, account, access, csrf_check)
    register_diary(app, db, account, access, csrf_check, render, patient_context)
    register_diary_library(app, db, account, csrf_check, render)
    register_intake(app, db, account, access, csrf_check, render, patient_context)
    register_portal(
        app, db, account, identity, access, csrf_check, render, patient_context
    )
    register_family(app, db, account, access, csrf_check, render, patient_context)
    register_recordings(app, db, account, access, csrf_check, render, has_consent)

    @app.post("/patients/{pid}/diary")
    async def diary(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        day: date = Form(...),
        wellbeing: int = Form(..., ge=0, le=10),
        sleep: float = Form(..., ge=0, le=24),
        sleep_quality: str | None = Form(None, max_length=32),
        adherence: str = Form(...),
        note: str = Form("", max_length=3000),
        contact: bool = Form(False),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        if day > date.today() or adherence not in ["taken", "missed", "none"]:
            raise HTTPException(422, "Проверьте дату и отметку выполнения назначения")
        quality = sleep_rating(sleep_quality)
        form = await request.form()
        prepared = {}
        with db() as c:
            assigned = {m["id"] for m in questions.definitions(c, pid)}
        for key in form:
            if not key.startswith("files_"):
                continue
            try:
                mid = int(key[6:])
            except ValueError:
                raise HTTPException(422, "Проверьте вопрос вложения") from None
            if mid not in assigned:
                raise HTTPException(422, "Вопрос вложения не назначен пациенту")
            for upload in form.getlist(key):
                if not getattr(upload, "filename", ""):
                    continue
                content = await upload.read(attachment_files.file_limit() + 1)
                prepared.setdefault(mid, []).append(
                    (
                        upload,
                        content,
                        await run_in_threadpool(
                            attachment_files.validate, content, upload.filename
                        ),
                    )
                )
        if (
            sum(len(items) for items in prepared.values()) > 10
            or sum(
                len(content) for items in prepared.values() for _, content, _ in items
            )
            > 3 * attachment_files.file_limit()
        ):
            raise HTTPException(422, "Слишком много вложений в одной записи")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            user = account(request)
            from .services.intake import check_scope

            check_scope(c, user, pid, write=True)
            extra_values = questions.parse(
                c,
                pid,
                form,
                day,
                dict(wellbeing=wellbeing, sleep=sleep, adherence=adherence),
                prepared,
            )
            did = c.execute(
                "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact,sleep_quality) VALUES(?,?,?,?,?,?,?,?)",
                (
                    pid,
                    day.isoformat(),
                    wellbeing,
                    sleep,
                    adherence,
                    note,
                    int(contact),
                    quality,
                ),
            ).lastrowid
            questions.save_answers(c, app.state.db_path, pid, user, did, extra_values)
            audit(c, user["id"], "diary_saved", pid, did)
        return RedirectResponse(f"/patients/{pid}/diary", status_code=303)

    @app.post("/patients/{pid}/screening")
    def screening(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        tension: int = Form(..., ge=0, le=10),
        difficulty: int = Form(..., ge=0, le=10),
        note: str = Form("", max_length=3000),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        with db() as c:
            c.execute(
                "INSERT INTO screenings(patient_id,day,tension,difficulty,note) VALUES(?,?,?,?,?)",
                (pid, date.today().isoformat(), tension, difficulty, note),
            )
        return back(pid)

    @app.post("/patients/{pid}/messages")
    async def retired_messages(request: Request, pid: int):
        access(request, pid)
        raise HTTPException(
            410,
            "Переписка закрыта. Используйте заметки в дневнике или запись на консультацию.",
        )

    @app.post("/patients/{pid}/risks")
    def risk(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        kind: str = Form(...),
        body: str = Form(..., min_length=1, max_length=3000),
        urgent: bool = Form(False),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        if kind not in ["event", "risk"] or not body.strip():
            raise HTTPException(422, "Проверьте описание события")
        with db() as c:
            c.execute(
                "INSERT INTO risks(patient_id,day,kind,body,urgent) VALUES(?,?,?,?,?)",
                (pid, date.today().isoformat(), kind, body.strip(), int(urgent)),
            )
        return back(pid)

    @app.post("/patients/{pid}/decisions")
    def decision(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        category: str = Form(...),
        body: str = Form(..., min_length=1, max_length=3000),
        source: str = Form(..., min_length=1, max_length=1000),
        confirmed: bool = Form(False),
    ):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Только врач подтверждает решения")
        csrf_check(request, csrf)
        if (
            not confirmed
            or category not in ["required", "recommended", "plan"]
            or not body.strip()
            or not source.strip()
        ):
            raise HTTPException(422, "Нужны категория, источник и подтверждение врача")
        with db() as c:
            c.execute(
                "INSERT INTO decisions(patient_id,day,category,body,source,confirmed) VALUES(?,?,?,?,?,1)",
                (pid, date.today().isoformat(), category, body.strip(), source.strip()),
            )
        return RedirectResponse(f"/patients/{pid}/workspace/safety", status_code=303)

    @app.get("/api/alerts")
    def alerts(request: Request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только для врача")
        with db() as c:
            rows = c.execute(
                """SELECT r.id,r.patient_id,p.name,r.day,r.kind,r.body,r.urgent,
                CASE WHEN r.id <= COALESCE(a.risk_id,0) THEN 1 ELSE 0 END acknowledged
                FROM risks r JOIN patients p ON p.id=r.patient_id
                LEFT JOIN acknowledgements a ON a.patient_id=r.patient_id
                WHERE p.doctor_id=? ORDER BY r.urgent DESC,r.id DESC""",
                (account(request)["doctor_id"],),
            ).fetchall()
        return {"alerts": [dict(row) for row in rows]}

    @app.post("/patients/{pid}/acknowledge")
    def acknowledge(
        request: Request, pid: int, csrf: str = Form(...), last_id: int = Form(0, ge=0)
    ):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Только для врача")
        csrf_check(request, csrf)
        with db() as c:
            if (
                last_id
                and not c.execute(
                    "SELECT id FROM risks WHERE id=? AND patient_id=?", (last_id, pid)
                ).fetchone()
            ):
                raise HTTPException(422, "Событие не принадлежит пациенту")
            c.execute(
                "INSERT INTO acknowledgements VALUES(?,?) ON CONFLICT(patient_id) DO UPDATE SET risk_id=MAX(acknowledgements.risk_id,excluded.risk_id)",
                (pid, last_id),
            )
        return RedirectResponse(f"/patients/{pid}/workspace/safety", status_code=303)

    def doctor_reference_access(request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Библиотека лекарств доступна специалисту")

    # Fixed draft routes must precede the drug-card /{slug} catch-all.
    register_medicine_drafts(
        app, db, account, doctor_reference_access, csrf_check, render
    )
    register_doctor_medicines(
        app, db, account, doctor_reference_access, csrf_check, render
    )
    register_drug_library(app, db, account, doctor_reference_access, csrf_check, render)

    @app.get("/reference/aviandr/archive")
    def aviandr_archive(request: Request):
        doctor_reference_access(request)
        return render(request, "aviandr.html")

    @app.get("/reference/aviandr")
    def aviandr_reference(request: Request):
        doctor_reference_access(request)
        return RedirectResponse("/reference/aviandr/archive", status_code=303)

    @app.get("/reference/aviandr/source")
    def aviandr_source(request: Request):
        doctor_reference_access(request)
        return FileResponse(
            ROOT / "references" / "aviandr_patient_leaflet.pdf",
            media_type="application/pdf",
            filename="aviandr_patient_leaflet.pdf",
        )

    register_workflows(
        app, db, account, identity, access, csrf_check, render, has_consent
    )
    register_settings(app, db, account, csrf_check, render, has_consent)
    register_meetings(app, db, account, access, csrf_check, render)
    register_experimental_ai(app, account, csrf_check, render)
    register_patient_ai(app, db, account, csrf_check, render)
    return app


app = create_app()
