"""Persistent meeting processing; scoped consent checked before every model step."""

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app.storage import now, audit, connect
from app.services.consent import require_processing_consent, context_access
from app.services.meeting_ai import (
    ProcessingError,
    LocalModels,
    configuration,
    config_stamp,
    validate_transcript,
    validate_summary,
)
from app.services.meeting_media import audio_for_model

logger = logging.getLogger(__name__)

ERRORS = {
    "asr_model_invalid": "Контрольная сумма локальной модели не подтверждена. Загрузите модель по инструкции заново.",
    "asr_model_missing": "Локальная модель Whisper отсутствует или загружена не полностью, включая словарь. Выполните: python -m scripts.setup_meeting_ai --download-whisper.",
    "asr_dependency_missing": "Установите зависимости из requirements-meetings.txt.",
    "local_provider_required": "Этот режим допускает только локальные модели и локальный Ollama.",
    "summary_provider_unavailable": "Ollama недоступна, модель не установлена или ответ не получен. Проверьте локальный сервис.",
    "invalid_transcript": "Модель вернула некорректную расшифровку или таймкоды.",
    "invalid_summary": "Ответ модели не прошёл проверку формата, цитат или ссылок на сегменты.",
    "empty_summary": "Модель не нашла подтверждённых пунктов в этом фрагменте.",
    "media_unavailable": "Не установлен FFmpeg.",
    "media_decode_failed": "Не удалось извлечь звук.",
    "permission_changed": "Согласия, состав участников, доступ, исходная версия или срок хранения изменились. Создайте новую задачу после проверки.",
    "configuration_changed": "Настройка моделей изменилась. Создайте новую задачу.",
    "worker_interrupted": "Обработчик был прерван. Запустите повтор после проверки.",
    "processing_failed": "Обработка не завершилась. Содержимое записи не включается в журнал ошибок.",
}


def stamp_time(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat(
        timespec="seconds"
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def recording(c, rid, user_id=None):
    row = c.execute(
        """SELECT r.id,r.meeting_id,r.filename,r.mime,r.duration,r.sha256,r.created_by,r.created_at,r.expires_at,r.deleted_at,
                     length(r.content) size,m.context_id,m.patient_id,m.appointment_id FROM meeting_recordings r
                     JOIN meeting_sessions m ON m.id=r.meeting_id WHERE r.id=?""",
        (rid,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Запись встречи не найдена")
    if user_id is not None:
        context_access(c, row["context_id"], user_id)
    if row["deleted_at"] or row["expires_at"] <= now() or not row["size"]:
        raise HTTPException(410, "Запись удалена или срок хранения истёк")
    return row


def latest(c, rid, kind):
    return c.execute(
        "SELECT * FROM meeting_artifacts WHERE recording_id=? AND kind=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
        (rid, kind),
    ).fetchone()


def permission_stamp(c, row, kind):
    purposes = [
        "video_recording" if row["mime"].startswith("video/") else "audio_recording",
        "ai_transcription",
    ]
    if kind == "summary":
        purposes.append("ai_summary")
    for purpose in purposes:
        require_processing_consent(c, row["context_id"], purpose)
    events = c.execute(
        """SELECT e.account_id,e.purpose,MAX(e.id) FROM consent_events e
                       WHERE e.context_id=? GROUP BY e.account_id,e.purpose ORDER BY e.account_id,e.purpose""",
        (row["context_id"],),
    ).fetchall()
    relevant = [tuple(r) for r in events if r["purpose"] in purposes]
    return hashlib.sha256(serialize(relevant).encode()).hexdigest()


def event(c, rid, action, actor=None, artifact=None, reason=""):
    c.execute(
        "INSERT INTO meeting_events(recording_id,artifact_id,account_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
        (rid, artifact, actor, action, reason, now()),
    )


def enqueue(c, rid, kind, user):
    if kind not in ("transcript", "summary"):
        raise HTTPException(422, "Неизвестный вид обработки")
    row = recording(c, rid, user["id"])
    if user["role"] != "doctor":
        raise HTTPException(403, "Обработку запускает ведущий врач")
    consent = permission_stamp(c, row, kind)
    try:
        config = config_stamp()
    except ProcessingError as e:
        raise HTTPException(503, ERRORS[e.code])
    source = latest(c, rid, "transcript") if kind == "summary" else None
    if kind == "summary" and not source:
        raise HTTPException(409, "Сначала нужна расшифровка записи")
    source_id = source["id"] if source else None
    key = hashlib.sha256(
        serialize([rid, kind, source_id, consent, config]).encode()
    ).hexdigest()
    old = c.execute("SELECT * FROM meeting_jobs WHERE job_key=?", (key,)).fetchone()
    if old:
        if old["status"] == "failed":
            c.execute(
                "UPDATE meeting_jobs SET status='queued',error_code='',updated_at=? WHERE id=?",
                (now(), old["id"]),
            )
            event(c, rid, "job_retried", user["id"], reason=str(old["id"]))
        elif old["status"] == "cancelled":
            raise HTTPException(
                409,
                "Старая задача отменена. Нужно новое согласование или исходная версия.",
            )
        return old["id"]
    ident = c.execute(
        """INSERT INTO meeting_jobs(recording_id,kind,source_id,job_key,consent_stamp,config_stamp,requested_by,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
        (rid, kind, source_id, key, consent, config, user["id"], now(), now()),
    ).lastrowid
    event(c, rid, "job_queued", user["id"], reason=str(ident))
    return ident


def save_artifact(c, rid, kind, payload, provider, actor=None, source=None, job=None):
    ident = c.execute(
        "INSERT INTO meeting_artifacts(recording_id,kind,source_id,job_id,author_id,provider,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, kind, source, job, actor, provider, serialize(payload), now()),
    ).lastrowid
    event(c, rid, "artifact_created", actor, ident)
    return ident


def published(c, rid):
    last = c.execute(
        "SELECT * FROM meeting_events WHERE recording_id=? AND action IN ('published','withdrawn') ORDER BY id DESC LIMIT 1",
        (rid,),
    ).fetchone()
    if not last or last["action"] != "published":
        return None
    artifact = c.execute(
        "SELECT * FROM meeting_artifacts WHERE id=? AND deleted_at IS NULL",
        (last["artifact_id"],),
    ).fetchone()
    transcript = latest(c, rid, "transcript")
    return (
        artifact
        if artifact and transcript and artifact["source_id"] == transcript["id"]
        else None
    )


def delete_recording(c, row, actor=None, reason="explicit_deletion"):
    c.execute(
        "UPDATE meeting_recordings SET content=NULL,deleted_at=? WHERE id=? AND deleted_at IS NULL",
        (now(), row["id"]),
    )
    c.execute(
        "UPDATE meeting_artifacts SET payload=NULL,deleted_at=? WHERE recording_id=? AND deleted_at IS NULL",
        (now(), row["id"]),
    )
    c.execute(
        "UPDATE meeting_jobs SET status='cancelled',error_code='permission_changed',updated_at=? WHERE recording_id=? AND status IN ('queued','running')",
        (now(), row["id"]),
    )
    event(c, row["id"], "deleted", actor, reason=reason)


def process_one(db_path, provider=None):
    """Separate worker. Lease recovery never automatically repeats an external call."""
    provider = provider or LocalModels()
    worker = secrets.token_urlsafe(24)
    with connect(db_path) as c:
        c.execute("BEGIN IMMEDIATE")
        for expired in c.execute(
            "SELECT * FROM meeting_recordings WHERE expires_at<=? AND deleted_at IS NULL",
            (now(),),
        ).fetchall():
            delete_recording(c, expired, reason="retention_expired")
        stale = c.execute(
            "SELECT * FROM meeting_jobs WHERE status='running' AND lease_until<?",
            (now(),),
        ).fetchall()
        for job in stale:
            c.execute(
                "UPDATE meeting_jobs SET status='failed',error_code='worker_interrupted',worker_token=NULL,updated_at=? WHERE id=?",
                (now(), job["id"]),
            )
            event(c, job["recording_id"], "worker_interrupted", reason=str(job["id"]))
        job = c.execute(
            "SELECT * FROM meeting_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not job:
            return False
        job = dict(job)
        c.execute(
            "UPDATE meeting_jobs SET status='running',attempts=attempts+1,worker_token=?,lease_until=?,updated_at=? WHERE id=?",
            (worker, stamp_time(600), now(), job["id"]),
        )

    def guard(c):
        current = c.execute(
            "SELECT * FROM meeting_jobs WHERE id=?", (job["id"],)
        ).fetchone()
        if current["status"] != "running" or current["worker_token"] != worker:
            raise ProcessingError("permission_changed")
        row = recording(c, job["recording_id"], job["requested_by"])
        if permission_stamp(c, row, job["kind"]) != job["consent_stamp"]:
            raise ProcessingError("permission_changed")
        if config_stamp() != job["config_stamp"]:
            raise ProcessingError("configuration_changed")
        if job["kind"] == "summary":
            source = latest(c, row["id"], "transcript")
            if not source or source["id"] != job["source_id"]:
                raise ProcessingError("permission_changed")
        return row

    def checkpoint():
        with connect(db_path) as c:
            guard(c)
            c.execute(
                "UPDATE meeting_jobs SET lease_until=? WHERE id=? AND worker_token=?",
                (stamp_time(600), job["id"], worker),
            )

    stage = "prepare"
    try:
        with connect(db_path) as c:
            c.execute("BEGIN IMMEDIATE")
            row = guard(c)
            duration = row["duration"]
            content = c.execute(
                "SELECT content FROM meeting_recordings WHERE id=?", (row["id"],)
            ).fetchone()[0]
            source = (
                c.execute(
                    "SELECT * FROM meeting_artifacts WHERE id=?", (job["source_id"],)
                ).fetchone()
                if job["source_id"]
                else None
            )
        checkpoint()
        if job["kind"] == "transcript":
            stage = "decode_audio"
            with audio_for_model(content) as path:
                stage = "transcribe"
                raw, name = provider.transcribe(path, checkpoint)
            stage = "validate_transcript"
            result = validate_transcript(raw, duration)
        else:
            stage = "summarize"
            segments = json.loads(source["payload"])["segments"]
            raw, name = provider.summarize(segments, checkpoint)
            # Each chunk was checked; validate the aggregate without dropping long meetings.
            result = {k: [] for k in ("summary", "next_steps", "clarifications")}
            for k, items in raw.items():
                if k not in result or not isinstance(items, list):
                    raise ProcessingError("invalid_summary")
                for item in items:
                    single = {g: ([item] if g == k else []) for g in result}
                    result[k].extend(validate_summary(single, segments)[k])
            if not any(result.values()):
                raise ProcessingError("empty_summary")
        checkpoint()
        stage = "save_result"
        with connect(db_path) as c:
            c.execute("BEGIN IMMEDIATE")
            guard(c)
            save_artifact(
                c,
                row["id"],
                job["kind"],
                result,
                name,
                source=job["source_id"],
                job=job["id"],
            )
            c.execute(
                "UPDATE meeting_jobs SET status='done',lease_until=NULL,worker_token=NULL,error_code='',updated_at=? WHERE id=?",
                (now(), job["id"]),
            )
            event(c, row["id"], "job_done", reason=str(job["id"]))
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, ProcessingError)
            else (
                "permission_changed"
                if isinstance(exc, HTTPException)
                else "processing_failed"
            )
        )
        if isinstance(exc, RuntimeError) and str(exc) in ERRORS:
            code = str(exc)
        if code == "processing_failed":
            logger.error(
                "Meeting processing failed: job=%s stage=%s exception_type=%s. "
                "For ASR, run: python -m scripts.setup_meeting_ai --check-transcription. "
                "Meeting contents and exception messages are omitted.",
                job["id"],
                stage,
                type(exc).__name__,
            )
        status = (
            "cancelled"
            if code in ("permission_changed", "configuration_changed")
            else "failed"
        )
        with connect(db_path) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE meeting_jobs SET status=?,error_code=?,lease_until=NULL,worker_token=NULL,updated_at=? WHERE id=? AND worker_token=?",
                (status, code, now(), job["id"], worker),
            )
            event(c, job["recording_id"], "job_" + status, reason=code)
    return True
