"""Optional local camera recordings, independent of questionnaire answers/scores."""

import re
from fastapi import Request, Form, File, UploadFile, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, Response
from .storage import now, audit

VIDEO_SCALES = {
    "hcl33",
    "hads",
}  # HADS uses this component when its source form is supplied.
MAX_VIDEO = 25 * 1024 * 1024
VIDEO_CONSENT_VERSION = "local-camera-v1"


def video_data(db, aid):
    with db() as c:
        choice = c.execute(
            "SELECT status FROM video_choices WHERE assessment_id=?", (aid,)
        ).fetchone()
        recordings = [
            dict(r)
            for r in c.execute(
                "SELECT id,mime,duration,created_at FROM video_recordings WHERE assessment_id=? AND deleted_at IS NULL ORDER BY id",
                (aid,),
            )
        ]
    return dict(
        video_choice=choice["status"] if choice else "offered",
        video_recordings=recordings,
    )


def register_recordings(app, db, account, access, csrf_check, render, has_consent):
    def scope(request, aid, write=False):
        with db() as c:
            row = c.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone()
        if not row:
            raise HTTPException(404, "Анкета не найдена")
        access(request, row["patient_id"], write)
        if row["scale"] not in VIDEO_SCALES:
            raise HTTPException(422, "Для этой анкеты видеозапись не предусмотрена")
        return dict(row)

    @app.get("/assessments/{aid}/video")
    def camera_page(request: Request, aid: int):
        a = scope(request, aid, True)
        return render(
            request, "camera.html", assessment=a, consent=True, **video_data(db, aid)
        )

    @app.post("/assessments/{aid}/video/choice")
    def choice(
        request: Request,
        aid: int,
        csrf: str = Form(...),
        intent: str = Form(...),
        video_consent: bool = Form(False),
        return_item: int = Form(1, ge=1, le=33),
    ):
        a = scope(request, aid, True)
        csrf_check(request, csrf)
        if intent not in ["accept", "decline"] or (
            intent == "accept" and not video_consent
        ):
            raise HTTPException(422, "Для записи нужно отдельное согласие")
        status = "accepted" if intent == "accept" else "declined"
        with db() as c:
            c.execute(
                "INSERT INTO video_choices(assessment_id,patient_id,status,consent_version,consent_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(assessment_id) DO UPDATE SET status=excluded.status,consent_version=excluded.consent_version,consent_at=excluded.consent_at,updated_at=excluded.updated_at",
                (
                    aid,
                    a["patient_id"],
                    status,
                    VIDEO_CONSENT_VERSION,
                    now() if status == "accepted" else None,
                    now(),
                ),
            )
            audit(c, account(request)["id"], "camera_" + status, a["patient_id"], aid)
        if "application/json" in request.headers.get("accept", ""):
            return {"status": status}
        return RedirectResponse(
            f"/assessments/{aid}?item={return_item}", status_code=303
        )

    @app.get("/assessments/{aid}/video/status")
    def status(request: Request, aid: int):
        a = scope(request, aid)
        if account(request)["role"] != "patient":
            raise HTTPException(403, "Статус записи доступен пациенту")
        data = video_data(db, aid)
        return JSONResponse(
            {
                "status": data["video_choice"],
                "participating": has_consent(a["patient_id"]),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/assessments/{aid}/video/recordings")
    async def upload(
        request: Request,
        aid: int,
        csrf: str = Form(...),
        duration: float = Form(..., gt=0, le=600),
        upload_id: str = Form(...),
        file: UploadFile = File(...),
    ):
        a = scope(request, aid, True)
        csrf_check(request, csrf)
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", upload_id):
            raise HTTPException(422, "Неверный идентификатор записи")
        content = await file.read(MAX_VIDEO + 1)
        mime = (file.content_type or "").split(";")[0]
        valid = (mime == "video/webm" and content.startswith(b"\x1a\x45\xdf\xa3")) or (
            mime == "video/mp4" and content[4:8] == b"ftyp"
        )
        if not valid or len(content) > MAX_VIDEO or len(content) < 16:
            raise HTTPException(422, "Нужна видеозапись WebM/MP4 размером до 25 МБ")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            choice_row = c.execute(
                "SELECT * FROM video_choices WHERE assessment_id=?", (aid,)
            ).fetchone()
            if (
                not choice_row
                or choice_row["status"] != "accepted"
                or not has_consent(a["patient_id"])
            ):
                raise HTTPException(
                    403, "Согласие на видеозапись не подтверждено или отозвано"
                )
            previous = c.execute(
                "SELECT * FROM video_recordings WHERE upload_id=?", (upload_id,)
            ).fetchone()
            if previous:
                if (
                    previous["assessment_id"] != aid
                    or previous["content"] != content
                    or previous["deleted_at"]
                ):
                    raise HTTPException(
                        409, "Этот фрагмент уже отправлялся с другими данными"
                    )
                rid = previous["id"]
            else:
                rid = c.execute(
                    "INSERT INTO video_recordings(assessment_id,patient_id,mime,duration,consent_at,created_at,content,upload_id) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        aid,
                        a["patient_id"],
                        mime,
                        duration,
                        choice_row["consent_at"],
                        now(),
                        content,
                        upload_id,
                    ),
                ).lastrowid
                audit(c, account(request)["id"], "camera_saved", a["patient_id"], rid)
        return {"id": rid, "url": f"/recordings/{rid}"}

    @app.get("/recordings/{rid}")
    def playback(request: Request, rid: int):
        with db() as c:
            row = c.execute(
                "SELECT * FROM video_recordings WHERE id=? AND deleted_at IS NULL",
                (rid,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Запись не найдена")
            access(request, row["patient_id"])
            audit(c, account(request)["id"], "camera_view", row["patient_id"], rid)
        ext = "webm" if row["mime"] == "video/webm" else "mp4"
        return Response(
            row["content"],
            media_type=row["mime"],
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'inline; filename="recording-{rid}.{ext}"',
            },
        )

    @app.post("/recordings/{rid}/delete")
    def delete(request: Request, rid: int, csrf: str = Form(...)):
        with db() as c:
            row = c.execute(
                "SELECT * FROM video_recordings WHERE id=?", (rid,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Запись не найдена")
            access(request, row["patient_id"], True)
            csrf_check(request, csrf)
            c.execute(
                "UPDATE video_recordings SET content=NULL,deleted_at=? WHERE id=?",
                (now(), rid),
            )
            audit(c, account(request)["id"], "camera_deleted", row["patient_id"], rid)
        return RedirectResponse(
            f"/assessments/{row['assessment_id']}/video", status_code=303
        )
