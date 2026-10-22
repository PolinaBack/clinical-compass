import json
from pathlib import Path
import re

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, Response, JSONResponse
from starlette.concurrency import run_in_threadpool
from app.storage import now, audit
from app.services import meetings as service
from app.services.meeting_media import MAX_BYTES, inspect_media
from app.services.meeting_ai import (
    readiness,
    GROUPS,
    validate_transcript,
    ProcessingError,
)
from app.services.meeting_progress import job_status
from app.services.consent import (
    create_consultation_context,
    context_access,
    documents,
    MEDIA_PURPOSES,
    record_consent,
    require_processing_consent,
    patient_media_permissions,
)


def register_meetings(app, db, account, access, csrf_check, render):
    def scope(c, mid, user):
        row = c.execute(
            "SELECT m.*,a.starts_at FROM meeting_sessions m JOIN appointments a ON a.id=m.appointment_id WHERE m.id=?",
            (mid,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Встреча не найдена")
        context_access(c, row["context_id"], user["id"])
        return row

    def clinician(user):
        if user["role"] != "doctor":
            raise HTTPException(403, "Это действие выполняет ведущий врач")

    def reply(mid):
        return RedirectResponse(f"/meetings/{mid}", status_code=303)

    def progress_view(row, duration):
        result = job_status(row, duration)
        result["error_message"] = (
            service.ERRORS.get(row["error_code"], service.ERRORS["processing_failed"])
            if row["error_code"]
            else ""
        )
        return result

    @app.get("/meetings/{mid}/jobs")
    def jobs(request: Request, mid: int):
        user = account(request)
        clinician(user)
        with db() as c:
            scope(c, mid, user)
            rows = c.execute(
                """SELECT j.*,r.duration FROM meeting_jobs j JOIN meeting_recordings r ON r.id=j.recording_id
                   WHERE r.meeting_id=? AND r.deleted_at IS NULL AND r.expires_at>? ORDER BY j.id DESC""",
                (mid, now()),
            ).fetchall()
            result = [progress_view(row, row["duration"]) for row in rows]
        return JSONResponse({"jobs": result}, headers={"Cache-Control": "no-store"})

    @app.get("/patients/{pid}/meetings")
    def index(request: Request, pid: int):
        access(request, pid)
        user = account(request)
        with db() as c:
            patient = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
            visits = c.execute(
                """SELECT a.*,m.id meeting_id FROM appointments a LEFT JOIN meeting_sessions m ON m.appointment_id=a.id
                               WHERE a.patient_id=? ORDER BY a.starts_at DESC,a.id DESC""",
                (pid,),
            ).fetchall()
        response = render(request, "meetings.html", patient=patient, visits=visits)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/patients/{pid}/appointments/{vid}/meeting")
    async def prepare(request: Request, pid: int, vid: int):
        if access(request, pid) != "doctor":
            raise HTTPException(403, "Встречу подготавливает врач")
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute(
                "SELECT a.id FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE a.id=? AND a.patient_id=? AND p.doctor_id=?",
                (vid, pid, user["doctor_id"]),
            ).fetchone():
                raise HTTPException(404, "Приём не найден")
            old = c.execute(
                "SELECT id FROM meeting_sessions WHERE appointment_id=?", (vid,)
            ).fetchone()
            if old:
                mid = old["id"]
            else:
                context = create_consultation_context(c, pid, user["id"])
                mid = c.execute(
                    "INSERT INTO meeting_sessions(appointment_id,patient_id,context_id,created_by,created_at) VALUES(?,?,?,?,?)",
                    (vid, pid, context, user["id"], now()),
                ).lastrowid
                audit(c, user["id"], "meeting_prepared", pid, mid)
        return reply(mid)

    @app.get("/meetings/{mid}")
    def meeting_page(request: Request, mid: int):
        user = account(request)
        with db() as c:
            meeting = scope(c, mid, user)
            docs = documents(c)
            approvals = patient_media_permissions(c, meeting["patient_id"])
            records = []
            for r in c.execute(
                "SELECT id,filename,mime,duration,created_at,expires_at,deleted_at FROM meeting_recordings WHERE meeting_id=? ORDER BY id DESC",
                (mid,),
            ):
                item = dict(r)
                item["expired"] = r["expires_at"] <= now()
                item["transcript"] = (
                    service.latest(c, r["id"], "transcript")
                    if user["role"] == "doctor"
                    else None
                )
                visible = service.published(c, r["id"])
                item["summary"] = (
                    service.latest(c, r["id"], "summary")
                    if user["role"] == "doctor"
                    else visible
                )
                item["published_id"] = visible["id"] if visible else None
                for key in ("transcript", "summary"):
                    if item[key]:
                        item[key] = {
                            **dict(item[key]),
                            "data": json.loads(item[key]["payload"]),
                        }
                if item["deleted_at"] or item["expired"]:
                    item["transcript"] = item["summary"] = None
                item["jobs"] = (
                    [
                        progress_view(job, r["duration"])
                        for job in c.execute(
                            "SELECT * FROM meeting_jobs WHERE recording_id=? ORDER BY id DESC",
                            (r["id"],),
                        ).fetchall()
                    ]
                    if user["role"] == "doctor"
                    else []
                )
                item["history"] = (
                    c.execute(
                        "SELECT id,kind,provider,created_at,deleted_at FROM meeting_artifacts WHERE recording_id=? ORDER BY id DESC",
                        (r["id"],),
                    ).fetchall()
                    if user["role"] == "doctor"
                    else []
                )
                records.append(item)
        response = render(
            request,
            "meeting.html",
            meeting=meeting,
            records=records,
            consent_docs=docs,
            approvals=approvals,
            purposes=MEDIA_PURPOSES,
            ai=readiness(),
            errors=service.ERRORS,
            groups=GROUPS,
            timecode=lambda seconds: f"{int(seconds)//60:02d}:{int(seconds)%60:02d}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/meetings/{mid}/consents/{purpose}")
    async def consent(request: Request, mid: int, purpose: str):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        try:
            document_id = int(form.get("document_id", ""))
            expected = int(form.get("expected_id", ""))
        except ValueError:
            raise HTTPException(422, "Неверная версия согласия")
        if purpose not in MEDIA_PURPOSES:
            raise HTTPException(422, "Неизвестное согласие")
        with db() as c:
            meeting = scope(c, mid, user)
            record_consent(
                c,
                user,
                purpose,
                meeting["context_id"],
                str(form.get("action", "")),
                document_id,
                expected,
                form.get("confirmed") == "true",
            )
        return reply(mid)

    @app.post("/meetings/{mid}/recordings")
    async def upload(request: Request, mid: int):
        user = account(request)
        clinician(user)
        form = await request.form(max_files=1, max_fields=5)
        csrf_check(request, str(form.get("csrf", "")))
        kind = str(form.get("media_kind", ""))
        if kind not in ("audio", "video"):
            raise HTTPException(422, "Выберите аудио или видео")
        file = form.get("file")
        if not file or not hasattr(file, "read"):
            raise HTTPException(422, "Выберите запись")
        with db() as c:
            meeting = scope(c, mid, user)
            require_processing_consent(c, meeting["context_id"], kind + "_recording")
        content = await file.read(MAX_BYTES + 1)
        await file.close()
        meta = await run_in_threadpool(inspect_media, content)
        if meta["mime"].startswith("video/") != (kind == "video"):
            raise HTTPException(
                422,
                "Фактический тип записи отличается от выбранного; проверьте согласие на нужный вид записи",
            )
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            meeting = scope(c, mid, user)
            require_processing_consent(c, meeting["context_id"], kind + "_recording")
            old = c.execute(
                "SELECT * FROM meeting_recordings WHERE meeting_id=? AND sha256=? AND deleted_at IS NULL",
                (mid, meta["sha256"]),
            ).fetchone()
            if old and old["expires_at"] <= now():
                service.delete_recording(c, old, reason="retention_expired")
                old = None
            if not old:
                rid = c.execute(
                    """INSERT INTO meeting_recordings(meeting_id,filename,mime,duration,sha256,content,created_by,created_at,expires_at)
                                 VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        mid,
                        Path(file.filename or "recording").name[:160],
                        meta["mime"],
                        meta["duration"],
                        meta["sha256"],
                        content,
                        user["id"],
                        now(),
                        service.stamp_time(30 * 86400),
                    ),
                ).lastrowid
                service.event(c, rid, "uploaded", user["id"])
                audit(
                    c,
                    user["id"],
                    "meeting_recording_uploaded",
                    meeting["patient_id"],
                    rid,
                )
        return reply(mid)

    @app.get("/meeting-recordings/{rid}/media")
    def media(request: Request, rid: int):
        user = account(request)
        clinician(user)
        with db() as c:
            row = service.recording(c, rid, user["id"])
            size = row["size"]
            start = 0
            end = size - 1
            status = 200
            header = request.headers.get("range")
            if header:
                match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", header)
                if not match or not any(match.groups()):
                    return Response(
                        status_code=416, headers={"Content-Range": f"bytes */{size}"}
                    )
                a, b = match.groups()
                if a:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                else:
                    start = max(0, size - int(b))
                if not 0 <= start <= end < size:
                    return Response(
                        status_code=416, headers={"Content-Range": f"bytes */{size}"}
                    )
                status = 206
            content = c.execute(
                "SELECT substr(content,?,?) FROM meeting_recordings WHERE id=?",
                (start + 1, end - start + 1, rid),
            ).fetchone()[0]
            audit(c, user["id"], "meeting_media_view", row["patient_id"], rid)
        headers = {
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(content)),
            "X-Content-Type-Options": "nosniff",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(
            content, media_type=row["mime"], status_code=status, headers=headers
        )

    @app.post("/meeting-recordings/{rid}/jobs/{kind}")
    async def enqueue(request: Request, rid: int, kind: str):
        user = account(request)
        clinician(user)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = service.recording(c, rid, user["id"])
            service.enqueue(c, rid, kind, user)
            audit(c, user["id"], "meeting_" + kind + "_queued", row["patient_id"], rid)
        return reply(row["meeting_id"])

    @app.post("/meeting-recordings/{rid}/transcript")
    async def edit_transcript(request: Request, rid: int):
        user = account(request)
        clinician(user)
        form = await request.form(max_fields=25000)
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = service.recording(c, rid, user["id"])
            service.permission_stamp(c, row, "transcript")
            base = service.latest(c, rid, "transcript")
            if not base or str(base["id"]) != form.get("revision"):
                raise HTTPException(409, "Расшифровка изменилась. Обновите страницу.")
            data = json.loads(base["payload"])
            for s in data["segments"]:
                s["text"] = str(form.get(f'text_{s["id"]}', "")).strip()
                s["speaker"] = str(form.get(f'speaker_{s["id"]}', "")).strip()
            try:
                checked = validate_transcript(data["segments"], row["duration"])
            except ProcessingError:
                raise HTTPException(
                    422,
                    "Проверьте непустой текст и обозначение говорящего; таймкоды не изменяются.",
                )
            ident = service.save_artifact(
                c,
                rid,
                "transcript",
                checked,
                "Правка врача",
                user["id"],
                source=base["id"],
            )
            service.event(
                c, rid, "withdrawn", user["id"], ident, reason="transcript_changed"
            )
        return reply(row["meeting_id"])

    @app.post("/meeting-recordings/{rid}/summary")
    async def edit_summary(request: Request, rid: int):
        user = account(request)
        clinician(user)
        form = await request.form(max_fields=25000)
        csrf_check(request, str(form.get("csrf", "")))
        intent = str(form.get("intent", ""))
        if intent not in ("draft", "publish", "withdraw"):
            raise HTTPException(422, "Выберите действие со сводкой")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = service.recording(c, rid, user["id"])
            if intent == "withdraw":
                service.event(
                    c, rid, "withdrawn", user["id"], reason="clinician_withdrawal"
                )
                return reply(row["meeting_id"])
            service.permission_stamp(c, row, "summary")
            base = service.latest(c, rid, "summary")
            transcript = service.latest(c, rid, "transcript")
            if (
                not base
                or str(base["id"]) != form.get("revision")
                or not transcript
                or base["source_id"] != transcript["id"]
            ):
                raise HTTPException(
                    409,
                    "Сводка или расшифровка изменилась. Сформируйте новый черновик.",
                )
            data = json.loads(base["payload"])
            for kind, items in data.items():
                for i, item in enumerate(items):
                    item["text"] = str(form.get(f"{kind}_{i}_text", "")).strip()
                    item["included"] = form.get(f"{kind}_{i}_included") == "yes"
                    if not 1 <= len(item["text"]) <= 1500:
                        raise HTTPException(
                            422,
                            "Пункт должен содержать от 1 до 1500 символов; исключите ненужное отметкой.",
                        )
            if intent == "publish":
                if (
                    form.get("sources_checked") != "yes"
                    or form.get("medicines_checked") != "yes"
                ):
                    raise HTTPException(
                        422,
                        "Нужна проверка источников и всех названий лекарств по оригиналу.",
                    )
                if not any(
                    item["included"]
                    for kind, items in data.items()
                    if kind != "clarifications"
                    for item in items
                ):
                    raise HTTPException(
                        422, "Нет выбранных итогов или следующих шагов для пациента"
                    )
            ident = service.save_artifact(
                c,
                rid,
                "summary",
                data,
                "Правка врача",
                user["id"],
                source=transcript["id"],
            )
            service.event(
                c,
                rid,
                "published" if intent == "publish" else "draft_saved",
                user["id"],
                ident,
                reason="sources_and_medicines_checked" if intent == "publish" else "",
            )
        return reply(row["meeting_id"])

    @app.get("/meeting-recordings/{rid}/export")
    def export(request: Request, rid: int):
        user = account(request)
        with db() as c:
            row = service.recording(c, rid, user["id"])
            summary = (
                service.latest(c, rid, "summary")
                if user["role"] == "doctor"
                else service.published(c, rid)
            )
            if user["role"] == "patient" and not summary:
                raise HTTPException(403, "Врач ещё не подтвердил сводку")
            transcript = (
                service.latest(c, rid, "transcript")
                if user["role"] == "doctor"
                else None
            )
            result = {
                "recording": rid,
                "transcript_version": transcript["id"] if transcript else None,
                "segments": (
                    json.loads(transcript["payload"])["segments"] if transcript else []
                ),
                "summary": json.loads(summary["payload"]) if summary else None,
                "warning": (
                    "Проверяйте названия лекарств и текст по исходной записи. Это не назначение лечения."
                    if user["role"] == "doctor"
                    else "Проверенные врачом итоги разговора. Это не назначение лечения."
                ),
            }
            if user["role"] == "patient":
                result["summary"] = {
                    k: [i for i in v if i["included"]]
                    for k, v in result["summary"].items()
                    if k != "clarifications"
                }
            audit(c, user["id"], "meeting_export", row["patient_id"], rid)
        return Response(
            json.dumps(result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="meeting-{rid}.json"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/meeting-artifacts/{aid}")
    def artifact_version(request: Request, aid: int):
        user = account(request)
        clinician(user)
        with db() as c:
            artifact = c.execute(
                """SELECT x.*,COALESCE(p.display_name,a.username,'Машинная обработка') author_name
                   FROM meeting_artifacts x LEFT JOIN accounts a ON a.id=x.author_id
                   LEFT JOIN account_profiles p ON p.account_id=a.id
                   WHERE x.id=? AND x.deleted_at IS NULL""",
                (aid,),
            ).fetchone()
            if not artifact:
                raise HTTPException(404, "Версия не найдена")
            row = service.recording(c, artifact["recording_id"], user["id"])
            payload = json.loads(artifact["payload"])
            audit(c, user["id"], "meeting_version_view", row["patient_id"], aid)
        response = render(
            request,
            "meeting_version.html",
            artifact=artifact,
            payload=payload,
            record=row,
            groups=GROUPS,
            timecode=lambda s: f"{int(s)//60:02d}:{int(s)%60:02d}",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/meeting-recordings/{rid}/delete")
    async def delete(request: Request, rid: int):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        if form.get("confirmed") != "yes":
            raise HTTPException(422, "Подтвердите удаление записи и её производных")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = service.recording(c, rid, user["id"])
            service.delete_recording(c, row, user["id"])
            audit(c, user["id"], "meeting_deleted", row["patient_id"], rid)
        return reply(row["meeting_id"])
