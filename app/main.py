"""Local synthetic demonstration. No diagnostic or medication rules."""

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

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
        yield

    app = FastAPI(title="Клинический компас — демо", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secrets.token_hex(32),
        same_site="strict",
        max_age=28800,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    def identity(request):
        user = request.session.get("user")
        if user not in ["doctor", "patient1", "patient2"]:
            raise HTTPException(401, "Выберите демороль на главной странице")
        return user

    def access(request, pid, write_patient=False):
        user = identity(request)
        if (user != "doctor" and user != f"patient{pid}") or (
            write_patient and user == "doctor"
        ):
            raise HTTPException(403, "Нет доступа")
        with db() as c:
            if not c.execute(
                "SELECT id FROM patients WHERE id=? AND doctor_id=1", (pid,)
            ).fetchone():
                raise HTTPException(404, "Пациент не найден")
        return user

    def csrf_check(request, token):
        if (
            not secrets.compare_digest(request.session.get("csrf", ""), token)
            or not token
        ):
            raise HTTPException(403, "Недействительный CSRF-токен. Обновите страницу.")

    def render(request, template, **context):
        request.session.setdefault("csrf", secrets.token_urlsafe(24))
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={
                "user": request.session.get("user"),
                "csrf": request.session["csrf"],
                **context,
            },
        )

    def back(pid):
        return RedirectResponse(f"/patients/{pid}", status_code=303)

    @app.get("/")
    def home(request: Request):
        return render(request, "home.html")

    @app.post("/login")
    def login(request: Request, role: str = Form(...), csrf: str = Form(...)):
        csrf_check(request, csrf)
        if role not in ["doctor", "patient1", "patient2"]:
            raise HTTPException(422, "Неизвестная роль")
        request.session.clear()
        request.session.update(user=role, csrf=secrets.token_urlsafe(24))
        return RedirectResponse(
            "/doctor" if role == "doctor" else f"/patients/{role[-1]}", status_code=303
        )

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form(...)):
        csrf_check(request, csrf)
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/doctor")
    def doctor(request: Request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Только для врача")
        with db() as c:
            patients = c.execute(
                """SELECT p.*, (SELECT count(*) FROM diary d WHERE d.patient_id=p.id AND (d.adherence='missed' OR d.contact=1)) alerts FROM patients p WHERE p.doctor_id=1"""
            ).fetchall()
        return render(request, "doctor.html", patients=patients)

    @app.get("/patients/{pid}")
    def patient(request: Request, pid: int):
        access(request, pid)
        with db() as c:
            p = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
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
        ordered = sorted(data["diary"], key=lambda r: (r["day"], r["id"]))
        points = " ".join(
            f'{30+i*540/max(len(ordered)-1,1):.1f},{170-r["wellbeing"]*14}'
            for i, r in enumerate(ordered)
        )
        return render(
            request,
            "patient.html",
            patient=p,
            today=date.today().isoformat(),
            points=points,
            **data,
        )

    @app.post("/patients/{pid}/diary")
    def diary(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        day: date = Form(...),
        wellbeing: int = Form(..., ge=0, le=10),
        sleep: float = Form(..., ge=0, le=24),
        adherence: str = Form(...),
        note: str = Form("", max_length=3000),
        contact: bool = Form(False),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        if day > date.today() or adherence not in ["taken", "missed", "none"]:
            raise HTTPException(422, "Проверьте дату и отметку выполнения назначения")
        with db() as c:
            c.execute(
                "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(?,?,?,?,?,?,?)",
                (pid, day.isoformat(), wellbeing, sleep, adherence, note, int(contact)),
            )
        return back(pid)

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
    def message(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        body: str = Form(..., min_length=1, max_length=3000),
    ):
        user = access(request, pid)
        csrf_check(request, csrf)
        if not body.strip():
            raise HTTPException(422, "Введите сообщение")
        with db() as c:
            c.execute(
                "INSERT INTO messages(patient_id,author,body,day) VALUES(?,?,?,?)",
                (
                    pid,
                    "Врач" if user == "doctor" else "Пациент",
                    body.strip(),
                    date.today().isoformat(),
                ),
            )
        return back(pid)

    @app.post("/patients/{pid}/relatives")
    def relative(
        request: Request,
        pid: int,
        csrf: str = Form(...),
        name: str = Form(..., min_length=1, max_length=80),
        relation: str = Form(...),
        parent_id: str = Form(""),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        if not name.strip() or relation not in [
            "self",
            "mother",
            "father",
            "sibling",
            "child",
            "other",
        ]:
            raise HTTPException(422, "Проверьте участника семьи")
        with db() as c:
            parent = None
            if parent_id:
                if (
                    not parent_id.isdigit()
                    or not c.execute(
                        "SELECT id FROM relatives WHERE id=? AND patient_id=?",
                        (int(parent_id), pid),
                    ).fetchone()
                ):
                    raise HTTPException(422, "Родитель должен принадлежать этой семье")
                parent = int(parent_id)
            c.execute(
                "INSERT INTO relatives(patient_id,name,relation,parent_id) VALUES(?,?,?,?)",
                (pid, name.strip(), relation, parent),
            )
        return back(pid)

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
        return back(pid)

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
                WHERE p.doctor_id=1 ORDER BY r.urgent DESC,r.id DESC"""
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
        return back(pid)

    def doctor_reference_access(request):
        if identity(request) != "doctor":
            raise HTTPException(403, "Справочник доступен демоврачу")

    @app.get("/reference/aviandr")
    def aviandr_reference(request: Request):
        doctor_reference_access(request)
        return render(request, "aviandr.html")

    @app.get("/reference/aviandr/source")
    def aviandr_source(request: Request):
        doctor_reference_access(request)
        return FileResponse(
            ROOT / "references" / "aviandr_patient_leaflet.pdf",
            media_type="application/pdf",
            filename="aviandr_patient_leaflet.pdf",
        )

    @app.get("/sources")
    def sources(request: Request):
        return render(request, "sources.html")

    return app


app = create_app()
