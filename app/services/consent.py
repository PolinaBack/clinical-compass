"""Patient permissions apply across meetings; history and live guards stay scoped."""

import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from app.storage import now, audit

MEDIA_PURPOSES = (
    "audio_recording",
    "video_recording",
    "ai_transcription",
    "ai_summary",
)
PURPOSES = ("notifications", *MEDIA_PURPOSES)
MEDIA_FIELDS = {
    "audio_recording": "audio",
    "video_recording": "video",
    "ai_transcription": "transcription",
    "ai_summary": "summary",
}


def account_context(c, account_id, create=False):
    row = c.execute(
        "SELECT context_id FROM account_consent_contexts WHERE account_id=?",
        (account_id,),
    ).fetchone()
    if row:
        return row["context_id"]
    if not create:
        return None
    user = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1 AND role='patient'",
        (account_id,),
    ).fetchone()
    if not user:
        raise HTTPException(
            403, "Разрешения записей меняет только пациент в своих настройках"
        )
    ident = secrets.token_urlsafe(24)
    c.execute(
        "INSERT INTO consultation_consent_contexts VALUES(?,?,?,?,NULL)",
        (ident, user["patient_id"], account_id, now()),
    )
    c.execute("INSERT INTO consent_participants VALUES(?,?)", (ident, account_id))
    c.execute("INSERT INTO account_consent_contexts VALUES(?,?)", (account_id, ident))
    return ident


def patient_media_permissions(c, patient_id):
    user = c.execute(
        "SELECT id FROM accounts WHERE patient_id=? AND role='patient' AND active=1",
        (patient_id,),
    ).fetchall()
    if len(user) != 1:
        return {p: False for p in MEDIA_PURPOSES}
    scope = account_context(c, user[0]["id"])
    context = (
        c.execute(
            "SELECT closed_at FROM consultation_consent_contexts WHERE id=?", (scope,)
        ).fetchone()
        if scope
        else None
    )
    return {
        p: bool(
            context
            and not context["closed_at"]
            and is_granted(c, user[0]["id"], p, scope)
        )
        for p in MEDIA_PURPOSES
    }


def cancel_patient_jobs(c, user, purposes):
    if user["role"] != "patient" or not purposes:
        return
    rows = c.execute(
        """SELECT j.id,j.kind,r.mime FROM meeting_jobs j JOIN meeting_recordings r ON r.id=j.recording_id
           JOIN meeting_sessions m ON m.id=r.meeting_id
           WHERE m.patient_id=? AND j.status IN ('queued','running')""",
        (user["patient_id"],),
    ).fetchall()
    for row in rows:
        media = (
            "video_recording" if row["mime"].startswith("video/") else "audio_recording"
        )
        if (
            media in purposes
            or "ai_transcription" in purposes
            or (row["kind"] == "summary" and "ai_summary" in purposes)
        ):
            c.execute(
                "UPDATE meeting_jobs SET status='cancelled',error_code='permission_changed',worker_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
            c.execute(
                "INSERT INTO meeting_events(recording_id,action,reason,created_at) SELECT recording_id,'job_cancelled','permission_changed',? FROM meeting_jobs WHERE id=?",
                (now(), row["id"]),
            )


def documents(c):
    return {
        row["purpose"]: dict(row)
        for row in c.execute(
            """SELECT d.* FROM consent_documents d
               WHERE d.id=(SELECT MAX(x.id) FROM consent_documents x WHERE x.purpose=d.purpose)"""
        )
    }


def latest_event(c, account_id, purpose, context_id=None):
    row = c.execute(
        """SELECT e.*,d.version FROM consent_events e
           JOIN consent_documents d ON d.id=e.document_id
           WHERE e.account_id=? AND e.purpose=? AND e.context_id IS ?
           ORDER BY e.id DESC LIMIT 1""",
        (account_id, purpose, context_id),
    ).fetchone()
    return dict(row) if row else None


def is_granted(c, account_id, purpose, context_id=None):
    if purpose not in PURPOSES:
        return False
    event = latest_event(c, account_id, purpose, context_id)
    document = documents(c).get(purpose)
    return bool(
        event
        and document
        and event["status"] == "granted"
        and event["document_id"] == document["id"]
    )


def context_access(c, context_id, account_id):
    """Only current participants linked to the patient's leading clinician."""
    context = c.execute(
        """SELECT x.* FROM consultation_consent_contexts x
           JOIN consent_participants p ON p.context_id=x.id
           JOIN accounts a ON a.id=p.account_id AND a.active=1
           WHERE x.id=? AND p.account_id=?""",
        (context_id, account_id),
    ).fetchone()
    if not context:
        raise HTTPException(403, "Нет доступа к согласиям этой консультации")
    invalid = c.execute(
        """SELECT 1 FROM consent_participants p
           JOIN accounts a ON a.id=p.account_id
           JOIN patients pt ON pt.id=?
           WHERE p.context_id=? AND (
             a.active=0 OR (a.role='doctor' AND a.doctor_id IS NOT pt.doctor_id)
             OR (a.role='patient' AND a.patient_id IS NOT pt.id)) LIMIT 1""",
        (context["patient_id"], context_id),
    ).fetchone()
    if invalid:
        raise HTTPException(
            403, "Состав участников изменился. Нужно новое согласование."
        )
    return dict(context)


def create_consultation_context(c, patient_id, doctor_account_id):
    """Internal hook for the future scheduling service, never an arbitrary form ID.

    Creates only a permission scope, not a video room, recording or appointment.
    Both real account participants are required; no permission is pre-granted.
    """
    doctor = c.execute(
        """SELECT a.id FROM accounts a JOIN patients p ON p.doctor_id=a.doctor_id
           WHERE p.id=? AND a.id=? AND a.role='doctor' AND a.active=1""",
        (patient_id, doctor_account_id),
    ).fetchone()
    patients = c.execute(
        "SELECT id FROM accounts WHERE patient_id=? AND role='patient' AND active=1",
        (patient_id,),
    ).fetchall()
    if not doctor or len(patients) != 1:
        raise HTTPException(403, "Нужны аккаунт пациента и его ведущий специалист")
    context_id = secrets.token_urlsafe(24)
    c.execute(
        "INSERT INTO consultation_consent_contexts VALUES(?,?,?,?,NULL)",
        (context_id, patient_id, doctor_account_id, now()),
    )
    c.executemany(
        "INSERT INTO consent_participants VALUES(?,?)",
        [(context_id, doctor_account_id), (context_id, patients[0]["id"])],
    )
    return context_id


def record_consent(
    c, user, purpose, context_id, action, document_id, expected_id, confirmed
):
    if purpose not in PURPOSES or action not in ("granted", "declined", "withdrawn"):
        raise HTTPException(422, "Недопустимый вид согласия или действие")
    if user["role"] != "patient":
        raise HTTPException(
            403,
            "Согласия на использование записей задаёт пациент; врачу отдельные согласия аккаунта не нужны",
        )
    if purpose == "notifications" and context_id is not None:
        raise HTTPException(422, "Уведомления настраиваются для аккаунта")
    if action == "granted" and not confirmed:
        raise HTTPException(
            422, "Подтвердите отдельное согласие после прочтения текста"
        )
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    if purpose in MEDIA_PURPOSES and context_id is None:
        context_id = account_context(c, user["id"], create=True)
    if context_id:
        participant = c.execute(
            "SELECT 1 FROM consent_participants WHERE context_id=? AND account_id=?",
            (context_id, user["id"]),
        ).fetchone()
        if not participant:
            raise HTTPException(403, "Нет доступа к согласиям этой консультации")
        # A changed clinician/closed context must never prevent withdrawing one's
        # own earlier decision. Only new grants require the current relationship.
        if action == "granted":
            if context_id != account_context(c, user["id"]):
                raise HTTPException(
                    409,
                    "Разрешения для всех встреч задаются один раз в настройках пациента",
                )
            context = context_access(c, context_id, user["id"])
            if context["closed_at"]:
                raise HTTPException(
                    409, "Эта консультация закрыта для новых разрешений"
                )
    current_document = documents(c)[purpose]
    if current_document["id"] != document_id:
        raise HTTPException(
            409, "Текст согласия обновлён. Прочитайте актуальную версию."
        )
    previous = latest_event(c, user["id"], purpose, context_id)
    if (previous["id"] if previous else 0) != expected_id:
        raise HTTPException(
            409, "Согласие изменилось. Обновите страницу перед подтверждением."
        )
    if (
        previous
        and previous["status"] == action
        and previous["document_id"] == document_id
    ):
        return previous["id"]
    event_id = c.execute(
        """INSERT INTO consent_events(account_id,context_id,purpose,document_id,status,created_at)
           VALUES(?,?,?,?,?,?)""",
        (user["id"], context_id, purpose, document_id, action, now()),
    ).lastrowid
    audit(c, user["id"], "optional_consent_" + action, user["patient_id"], event_id)
    if purpose in MEDIA_PURPOSES and action != "granted":
        cancel_patient_jobs(c, user, {purpose})
    return event_id


def participation_active(c, patient_id):
    row = c.execute(
        "SELECT accepted,adult FROM consents WHERE patient_id=? ORDER BY id DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    return bool(row and row["accepted"] and row["adult"])


def withdraw_optional_consents(c, user):
    """Withdrawal of participation must not silently reactivate old permissions later."""
    events = c.execute(
        """SELECT e.* FROM consent_events e WHERE e.account_id=? AND e.status='granted'
           AND e.id=(SELECT MAX(x.id) FROM consent_events x
                     WHERE x.account_id=e.account_id AND x.purpose=e.purpose
                     AND x.context_id IS e.context_id)""",
        (user["id"],),
    ).fetchall()
    for event in events:
        event_id = c.execute(
            """INSERT INTO consent_events(account_id,context_id,purpose,document_id,status,confirmed_via,created_at)
               VALUES(?,?,?,?,'withdrawn','participation_withdrawal',?)""",
            (
                user["id"],
                event["context_id"],
                event["purpose"],
                event["document_id"],
                now(),
            ),
        ).lastrowid
        audit(c, user["id"], "optional_consent_withdrawn", user["patient_id"], event_id)
    cancel_patient_jobs(c, user, set(MEDIA_PURPOSES))


def require_processing_consent(c, context_id, purpose):
    """Current patient's account permission, current relationship, active participation."""
    if purpose not in MEDIA_PURPOSES:
        raise HTTPException(422, "Неизвестный вид обработки")
    context = c.execute(
        "SELECT * FROM consultation_consent_contexts WHERE id=?", (context_id,)
    ).fetchone()
    if (
        not context
        or context["closed_at"]
        or not participation_active(c, context["patient_id"])
    ):
        raise HTTPException(403, "Обработка не разрешена или участие отозвано")
    participants = c.execute(
        """SELECT a.* FROM consent_participants p JOIN accounts a ON a.id=p.account_id
           WHERE p.context_id=?""",
        (context_id,),
    ).fetchall()
    if len(participants) != 2 or {p["role"] for p in participants} != {
        "doctor",
        "patient",
    }:
        raise HTTPException(403, "Состав участников не подтверждён")
    for participant in participants:
        context_access(c, context_id, participant["id"])
    if not patient_media_permissions(c, context["patient_id"])[purpose]:
        raise HTTPException(
            403, "Пациент не разрешил это действие в настройках записей встреч"
        )


def notifications_allowed(c, account_id, category, at=None):
    """Categories and quiet hours; patients additionally need their permission."""
    if category not in ("diary", "medication", "tasks"):
        return False
    user = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (account_id,)
    ).fetchone()
    if not user or (
        user["role"] == "patient" and not is_granted(c, account_id, "notifications")
    ):
        return False
    if user["role"] == "patient" and not participation_active(c, user["patient_id"]):
        return False
    prefs = c.execute(
        "SELECT * FROM notification_preferences WHERE account_id=?", (account_id,)
    ).fetchone()
    if not prefs or not prefs[category]:
        return False
    if prefs["quiet_enabled"]:
        profile = c.execute(
            "SELECT timezone FROM account_profiles WHERE account_id=?", (account_id,)
        ).fetchone()
        stamp = at or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            raise ValueError("Notification time must have a timezone")
        local = stamp.astimezone(
            ZoneInfo(profile["timezone"] if profile else "Europe/Moscow")
        )
        clock = local.strftime("%H:%M")
        start, end = prefs["quiet_start"], prefs["quiet_end"]
        quiet = start <= clock < end if start < end else clock >= start or clock < end
        if quiet:
            return False
    return True
