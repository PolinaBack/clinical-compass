"""Shared meeting presentation for calendar embedding and the patient page."""

import json
from fastapi import HTTPException
from app.storage import now
from app.services import meetings as service
from app.services.consent import (
    context_access,
    documents,
    patient_media_permissions,
    MEDIA_PURPOSES,
)
from app.services.meeting_ai import readiness, GROUPS
from app.services.meeting_progress import job_status
from app.services.meeting_summary import unique_summary


def recording_status(record, doctor):
    if record["deleted_at"] or record["expired"]:
        return dict(state="unavailable", label="Запись недоступна")
    if not doctor:
        return dict(
            state="ready" if record["published_id"] else "idle",
            label=(
                "Итоги опубликованы"
                if record["published_id"]
                else "Итоги ещё не опубликованы"
            ),
        )
    jobs = record["jobs"]
    job = (
        next((j for j in jobs if j["status"] == "running"), None)
        or next((j for j in jobs if j["status"] == "queued"), None)
        or (jobs[0] if jobs else None)
    )
    if job:
        title = "Расшифровка" if job["kind"] == "transcript" else "Подготовка итогов"
        label = title + ": " + job["status_label"]
        if job["status"] == "done":
            label = (
                "Расшифровка готова" if job["kind"] == "transcript" else "Итоги готовы"
            )
        if job["status"] in ("queued", "running"):
            label += " · " + job["stage_label"]
        if job["status"] == "running" and job["progress_text"]:
            label += " · " + job["progress_text"]
        return dict(state=job["status"], label=label)
    if record["summary"]:
        return dict(state="done", label="Итоги готовы")
    if record["transcript"]:
        return dict(state="done", label="Расшифровка готова")
    return dict(state="idle", label="Файл загружен · расшифровка не запущена")


def meeting_context(c, mid, user):
    meeting = c.execute(
        "SELECT m.*,a.starts_at FROM meeting_sessions m JOIN appointments a ON a.id=m.appointment_id WHERE m.id=?",
        (mid,),
    ).fetchone()
    if not meeting:
        raise HTTPException(404, "Встреча не найдена")
    context_access(c, meeting["context_id"], user["id"])

    def progress_view(row, duration):
        result = job_status(row, duration)
        result["error_message"] = (
            service.ERRORS.get(row["error_code"], service.ERRORS["processing_failed"])
            if row["error_code"]
            else ""
        )
        return result

    docs = documents(c)
    approvals = patient_media_permissions(c, meeting["patient_id"])
    records = []
    for r in c.execute(
        "SELECT id,filename,mime,duration,created_at,expires_at,deleted_at FROM meeting_recordings WHERE meeting_id=? ORDER BY id",
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
        item["number"] = len(records) + 1
        if item["summary"]:
            item["summary"]["display"] = unique_summary(
                item["summary"]["data"], included_only=True
            )
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
        item["list_status"] = recording_status(item, user["role"] == "doctor")
        records.append(item)
    return dict(
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
