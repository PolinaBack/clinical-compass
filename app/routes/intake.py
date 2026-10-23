"""About-me intake is separate from clinical scales and the medication prescription."""

import json
import secrets
from datetime import date
from urllib.parse import quote
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from app.intake_catalog import (
    VERSION,
    CATEGORIES,
    BY_KEY,
    ENTITY_FIELDS,
    ENTITY_CATEGORY,
    ENTITY_LABELS,
    STATES,
    SYSTEMS,
    SPECIALTIES,
)
from app.services import intake as service, clinical_attachments as files
from app.services.clinical_forms import visible, display, today
from app.storage import now, audit


def intake_context(c, user, pid, view="summary", submission_id=None, as_of=None):
    service.check_scope(c, user, pid)
    submission = service.read_submission(c, pid, submission_id)
    current = service.draft(c, pid) if user["role"] == "patient" else None
    data = (
        service.snapshot(c, pid)
        if current and submission_id is None
        else (
            submission["data"]
            if submission
            else dict(
                answers={},
                entities=[],
                attachments=[],
                links=[],
                preferences=service.preferences(c, pid),
            )
        )
    )
    prefs = data.get("preferences", service.preferences(c, pid))
    blocks, deferred = service.overview(data, prefs)
    if as_of:
        try:
            reference = date.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(422, "Проверьте дату расчёта возраста") from None
    else:
        visit = c.execute(
            "SELECT starts_at FROM appointments WHERE patient_id=? AND starts_at>=? ORDER BY starts_at LIMIT 1",
            (pid, today().isoformat()),
        ).fetchone()
        try:
            reference = (
                date.fromisoformat(visit["starts_at"][:10]) if visit else today()
            )
        except ValueError:
            reference = today()
    reviews = {}
    if submission:
        for row in c.execute(
            "SELECT r.*,a.username FROM intake_reviews r JOIN accounts a ON a.id=r.actor_id WHERE submission_id=? ORDER BY r.id",
            (submission["id"],),
        ):
            reviews[row["target"]] = dict(row)
    review_history = (
        [
            dict(r)
            for r in c.execute(
                "SELECT r.*,a.username FROM intake_reviews r JOIN accounts a ON a.id=r.actor_id JOIN intake_submissions s ON s.id=r.submission_id WHERE s.patient_id=? ORDER BY r.id DESC",
                (pid,),
            )
        ]
        if user["role"] == "doctor"
        else []
    )
    history = [
        dict(r)
        for r in c.execute(
            "SELECT id,revision,catalog_version,created_at FROM intake_submissions WHERE patient_id=? ORDER BY id DESC",
            (pid,),
        )
    ]
    progress = service.categories_with_progress(data, prefs)
    counted = [c for c in progress if c["total"]]
    complete = sum(c["done"] == c["total"] for c in counted)
    quotes = [
        "Можно заполнять в своём темпе — даже несколько ответов помогут начать разговор.",
        "Каждый сохранённый ответ помогает лучше подготовиться к встрече.",
        "Не обязательно вспоминать всё сразу. К любому вопросу можно вернуться.",
    ]
    return dict(
        intake_overall=dict(
            completed=complete,
            total=len(counted),
            done=sum(c["done"] for c in counted),
            questions=sum(c["total"] for c in counted),
            quote=quotes[sum(c["done"] for c in counted) % len(quotes)],
        ),
        intake_review_history=review_history,
        intake_groups=list(
            dict.fromkeys(
                c["group"]
                for c in data.get("catalog", {}).get("categories", CATEGORIES)
            )
        ),
        intake_readonly=user["role"] == "doctor" or submission_id is not None,
        intake_data=data,
        intake_draft=current,
        intake_submission=submission,
        intake_preferences=prefs,
        intake_categories=progress,
        intake_blocks=blocks,
        intake_deferred=deferred,
        intake_calculations=service.computations(data, reference),
        intake_reviews=reviews,
        intake_history=history,
        intake_view=view,
        intake_states=STATES,
        intake_version=VERSION,
        entity_labels=data.get("catalog", {}).get("entity_labels", ENTITY_LABELS),
        entity_category=data.get("catalog", {}).get("entity_category", ENTITY_CATEGORY),
        intake_visible=visible,
        intake_display=display,
        intake_today=today().isoformat(),
        intake_entity_fields=data.get("catalog", {}).get("entity_fields", {}),
        intake_systems=SYSTEMS,
        intake_specialties=SPECIALTIES,
    )


def register_intake(app, db, account, access, csrf_check, render, patient_context):
    def redirect(pid, tail=""):
        return RedirectResponse(
            f"/patients/{pid}/profile/about" + tail, status_code=303
        )

    def context(request, pid, view="summary", submission_id=None):
        access(request, pid)
        result = patient_context(request, pid)
        with db() as c:
            result.update(
                intake_context(
                    c,
                    account(request),
                    pid,
                    view,
                    submission_id,
                    request.query_params.get("as_of"),
                )
            )
        return result

    @app.get("/patients/{pid}/intake/medication-names")
    def medication_names(request: Request, pid: int, q: str = ""):
        access(request, pid)
        from app.services import drug_library

        with db() as c:
            service.check_scope(c, account(request), pid)
            names = (
                [row["name"] for row in drug_library.search(c, q[:100])[:12]]
                if len(q.strip()) >= 2
                else []
            )
        return JSONResponse(dict(names=names), headers={"Cache-Control": "no-store"})

    @app.get("/patients/{pid}/profile/about/{category}")
    def section(
        request: Request, pid: int, category: str, submission_id: int | None = None
    ):
        data = context(request, pid, submission_id=submission_id)
        if category == "family" and submission_id is None:
            return RedirectResponse(f"/patients/{pid}/profile/family", status_code=303)
        category_definition = next(
            (c for c in data["intake_categories"] if c["key"] == category), None
        )
        if not category_definition:
            raise HTTPException(404, "Раздел анкеты не найден")
        return render(
            request, "intake_section.html", category=category_definition, **data
        )

    @app.post("/patients/{pid}/intake/sections/{category}")
    async def save_section(request: Request, pid: int, category: str):
        access(request, pid, True)
        if category not in BY_KEY:
            raise HTTPException(404, "Раздел не найден")
        form = await request.form(max_fields=3000)
        csrf_check(request, str(form.get("csrf", "")))
        try:
            date.fromisoformat(str(form.get("as_of", today().isoformat())))
        except ValueError:
            raise HTTPException(422, "Проверьте дату расчёта возраста") from None
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            revision = service.save_category(c, pid, account(request), category, form)
            reference = date.fromisoformat(str(form.get("as_of", today().isoformat())))
            snapshot = service.snapshot(c, pid)
            calculations = service.computations(snapshot, reference)
            progress = next(
                item
                for item in service.categories_with_progress(
                    snapshot, snapshot["preferences"]
                )
                if item["key"] == category
            )
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                dict(
                    saved=True,
                    revision=revision,
                    calculations=calculations,
                    progress=dict(done=progress["done"], total=progress["total"]),
                )
            )
        return redirect(pid, "/" + category + "?saved=1")

    @app.get("/patients/{pid}/intake/entities/new/{kind}")
    def new_entity(request: Request, pid: int, kind: str, related: int | None = None):
        access(request, pid, True)
        if kind not in ENTITY_FIELDS:
            raise HTTPException(404, "Тип карточки не найден")
        data = context(request, pid)
        with db() as c:
            entity_definitions = service.entity_fields(c, pid, kind)
        if related is not None and not any(
            e["id"] == related for e in data["intake_data"]["entities"]
        ):
            raise HTTPException(404, "Связанная карточка не найдена")
        return render(
            request,
            "intake_entity.html",
            client_token=secrets.token_urlsafe(24),
            entity=None,
            entity_kind=kind,
            entity_fields=entity_definitions,
            entity_answers={},
            entity_progress=service.question_progress(entity_definitions, {}),
            related=[related] if related is not None else [],
            **data,
        )

    @app.get("/patients/{pid}/intake/entities/{eid}")
    def entity_page(
        request: Request, pid: int, eid: int, submission_id: int | None = None
    ):
        data = context(request, pid, submission_id=submission_id)
        entity = next(
            (e for e in data["intake_data"]["entities"] if e["id"] == eid), None
        )
        if not entity:
            raise HTTPException(404, "Карточка не найдена или ещё не отправлена врачу")
        with db() as c:
            entity_definitions = data["intake_entity_fields"].get(
                entity["kind"]
            ) or service.entity_fields(c, pid, entity["kind"])
        related = [
            link["right_id"] if link["left_id"] == eid else link["left_id"]
            for link in data["intake_data"]["links"]
            if eid in (link["left_id"], link["right_id"])
        ]
        return render(
            request,
            "intake_entity.html",
            entity=entity,
            entity_kind=entity["kind"],
            entity_fields=entity_definitions,
            entity_answers=entity["data"],
            entity_progress=service.question_progress(
                entity_definitions, entity["data"]
            ),
            related=related,
            **data,
        )

    @app.post("/patients/{pid}/intake/entities/{kind}/save")
    async def save_entity(request: Request, pid: int, kind: str):
        access(request, pid, True)
        if kind not in ENTITY_FIELDS:
            raise HTTPException(404, "Тип карточки не найден")
        form = await request.form(max_fields=3000)
        csrf_check(request, str(form.get("csrf", "")))
        try:
            eid = int(form["entity_id"]) if form.get("entity_id") else None
        except ValueError:
            raise HTTPException(422, "Проверьте карточку") from None
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            eid = service.save_entity(c, pid, account(request), kind, form, eid)
            current = service.draft(c, pid)
            saved_entity = next(e for e in service.entities(c, pid) if e["id"] == eid)
            entity_progress = service.question_progress(
                service.entity_fields(c, pid, kind), saved_entity["data"]
            )
            version = c.execute(
                "SELECT version FROM intake_entities WHERE id=?", (eid,)
            ).fetchone()[0]
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                dict(
                    saved=True,
                    revision=current["revision"],
                    entity_id=eid,
                    entity_version=version,
                    progress=entity_progress,
                )
            )
        return RedirectResponse(
            f"/patients/{pid}/intake/entities/{eid}?saved=1", status_code=303
        )

    @app.post("/patients/{pid}/intake/submit")
    async def submit(request: Request, pid: int):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            sid = service.submit(c, pid, account(request), form)
        return redirect(pid, "?submitted=" + str(sid))

    @app.post("/patients/{pid}/intake/review")
    async def review(request: Request, pid: int):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Проверка доступна ведущему врачу")
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            service.review(c, pid, account(request), form)
        return redirect(pid, "?reviewed=1")

    @app.post("/patients/{pid}/intake/preferences")
    async def preferences(request: Request, pid: int):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Настройка доступна ведущему врачу")
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            user = account(request)
            service.check_scope(c, user, pid, doctor=True)
            old = service.preferences(c, pid)
            if str(old["revision"]) != str(form.get("revision", "")):
                raise HTTPException(409, "Настройка изменилась")
            c.execute(
                "INSERT INTO intake_preferences VALUES(?,?,?,?,?,?) ON CONFLICT(patient_id) DO UPDATE SET hide_weight=excluded.hide_weight,hide_nutrition=excluded.hide_nutrition,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (
                    pid,
                    int(form.get("hide_weight") == "yes"),
                    int(form.get("hide_nutrition") == "yes"),
                    old["revision"] + 1,
                    user["id"],
                    now(),
                ),
            )
            # A concurrent patient form must reload after a clinical visibility change.
            current = service.draft(c, pid)
            service.save_draft(
                c,
                pid,
                user,
                current,
                current["answers"],
                "preferences_changed",
                dict(
                    hide_weight=form.get("hide_weight") == "yes",
                    hide_nutrition=form.get("hide_nutrition") == "yes",
                ),
            )
        return redirect(pid, "?preferences_saved=1")

    @app.post("/patients/{pid}/intake/files")
    async def upload(request: Request, pid: int):
        access(request, pid, True)
        async with request.form(max_files=10, max_fields=100) as form:
            csrf_check(request, str(form.get("csrf", "")))
            category = str(form.get("category", ""))
            if category not in BY_KEY:
                raise HTTPException(422, "Выберите раздел файла")
            try:
                eid = int(form["entity_id"]) if form.get("entity_id") else None
            except ValueError:
                raise HTTPException(422, "Проверьте карточку файла") from None
            uploads = form.getlist("files")
            if not 1 <= len(uploads) <= 10:
                raise HTTPException(422, "Выберите от 1 до 10 файлов")
            contents = []
            for upload in uploads:
                if not hasattr(upload, "read"):
                    raise HTTPException(422, "Нужен файл")
                content = await upload.read(files.file_limit() + 1)
                contents.append(
                    (
                        upload,
                        content,
                        await run_in_threadpool(
                            files.validate, content, upload.filename or ""
                        ),
                    )
                )
                if sum(len(item[1]) for item in contents) > 3 * files.file_limit():
                    raise HTTPException(
                        422, "Суммарный размер файлов превышает три лимита одного файла"
                    )
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                user = account(request)
                service.check_scope(c, user, pid, write=True)
                current = service.draft(c, pid)
                service.revision_check(current, form)
                if (
                    eid
                    and not c.execute(
                        "SELECT id FROM intake_entities WHERE id=? AND patient_id=? AND archived=0",
                        (eid, pid),
                    ).fetchone()
                ):
                    raise HTTPException(422, "Карточка принадлежит другому пациенту")
                ids = [
                    files.save(
                        c,
                        app.state.db_path,
                        pid,
                        user,
                        "intake",
                        category,
                        eid,
                        u,
                        content,
                        form,
                        validated=validation,
                    )
                    for u, content, validation in contents
                ]
                service.save_draft(
                    c,
                    pid,
                    user,
                    current,
                    current["answers"],
                    "files_added",
                    dict(file_ids=ids),
                )
        return redirect(pid, "/" + category + "?files_saved=1")

    def scoped_file(c, user, pid, fid):
        service.check_scope(c, user, pid)
        row = c.execute(
            "SELECT * FROM clinical_attachments WHERE id=? AND patient_id=? AND deleted_at IS NULL",
            (fid, pid),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Файл не найден")
        if user["role"] == "doctor":
            allowed = False
            if row["scope"] == "intake":
                for s in c.execute(
                    "SELECT payload FROM intake_submissions WHERE patient_id=?", (pid,)
                ):
                    if any(
                        f["id"] == fid for f in json.loads(s["payload"])["attachments"]
                    ):
                        allowed = True
                        break
            else:
                allowed = any(
                    fid in json.loads(a["answer_json"])
                    for a in c.execute(
                        "SELECT d.answer_json FROM diary_answer_details d JOIN diary x ON x.id=d.diary_id WHERE x.patient_id=? AND d.metric_id=? AND d.status='answered'",
                        (pid, int(row["category"])),
                    )
                    if isinstance(json.loads(a["answer_json"]), list)
                )
            if not allowed:
                raise HTTPException(403, "Пациент ещё не отправил этот файл")
        return row

    @app.get("/patients/{pid}/clinical-files/{fid}")
    def file_page(request: Request, pid: int, fid: int):
        access(request, pid)
        user = account(request)
        with db() as c:
            row = scoped_file(c, user, pid, fid)
            audit(c, user["id"], "clinical_file_metadata_viewed", pid, fid)
        return render(request, "clinical_file.html", file=dict(row), patient_id=pid)

    @app.get("/patients/{pid}/clinical-files/{fid}/content")
    def file_content(
        request: Request,
        pid: int,
        fid: int,
        download: bool = False,
        ack_unscanned: bool = False,
    ):
        access(request, pid)
        user = account(request)
        with db() as c:
            row = scoped_file(c, user, pid, fid)
            if row["scan_status"] == "infected":
                raise HTTPException(
                    403, "Антивирус обнаружил угрозу. Скачивание заблокировано."
                )
            if row["scan_status"] != "clean" and not (download and ack_unscanned):
                raise HTTPException(
                    409,
                    "Антивирусная проверка не выполнена. Автоматический просмотр выключен; доступно явное скачивание оригинала на странице файла.",
                )
            content = files.decrypt(c, app.state.db_path, row)
            audit(c, user["id"], "clinical_file_opened", pid, fid)
        return Response(
            content,
            media_type=row["mime"] if not download else "application/octet-stream",
            headers={
                "Content-Disposition": ("attachment" if download else "inline")
                + "; filename*=UTF-8''"
                + quote(row["filename"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )

    @app.post("/patients/{pid}/clinical-files/{fid}/delete")
    async def delete_file(request: Request, pid: int, fid: int):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        if form.get("confirmed") != "yes":
            raise HTTPException(422, "Подтвердите удаление файла")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            user = account(request)
            service.check_scope(c, user, pid, write=True)
            row = scoped_file(c, user, pid, fid)
            if row["scope"] != "intake":
                raise HTTPException(
                    422, "Для этой записи используется история дневника"
                )
            if any(
                any(f["id"] == fid for f in json.loads(s["payload"])["attachments"])
                for s in c.execute(
                    "SELECT payload FROM intake_submissions WHERE patient_id=?", (pid,)
                )
            ):
                raise HTTPException(
                    409,
                    "Файл уже отправлен. Можно уточнить сведения новой версией анкеты; прежний источник не удаляется из истории этой кнопкой.",
                )
            current = service.draft(c, pid)
            service.revision_check(current, form)
            c.execute(
                "UPDATE clinical_attachments SET content=NULL,deleted_at=? WHERE id=?",
                (now(), fid),
            )
            service.save_draft(
                c,
                pid,
                user,
                current,
                current["answers"],
                "file_deleted",
                dict(file_id=fid),
                fid,
            )
        return redirect(pid, "/" + row["category"])
