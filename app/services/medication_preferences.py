"""Patient-authored tolerability preferences, independent from private intake data."""

import json
import secrets
from fastapi import HTTPException
from app.adverse_catalog import CATALOG, ELIGIBLE, OPTIONS
from app.services import clinical_attachments as files, care_orders
from app.storage import audit, now


def decode(c, path, row):
    try:
        value = json.loads(files.cipher(path, c).decrypt(row["payload"]))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503,
            "Не удалось открыть пожелания прежним ключом. Восстановите защищённую резервную копию.",
        ) from None
    return dict(
        value,
        revision=row["revision"],
        status=row["status"],
        created_at=row["created_at"],
    )


def sent(c, path, pid):
    row = c.execute(
        "SELECT * FROM medication_preference_versions WHERE patient_id=? AND status='sent' ORDER BY revision DESC LIMIT 1",
        (pid,),
    ).fetchone()
    return decode(c, path, row) if row else None


def patient_scope(c, user, pid):
    care_orders.scope(c, user, pid)
    if user["role"] != "patient":
        raise HTTPException(403, "Пожелания заполняет только сам пациент")


def assignment(c, pid):
    row = c.execute(
        "SELECT * FROM medication_preference_requests WHERE patient_id=? ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()
    return dict(row) if row else None


def assign(c, user, pid, token=None):
    care_orders.scope(c, user, pid, doctor=True)
    token = token or secrets.token_urlsafe(24)
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise HTTPException(422, "Обновите форму назначения анкеты")
    previous = c.execute(
        "SELECT * FROM medication_preference_requests WHERE client_token=?", (token,)
    ).fetchone()
    if previous:
        if previous["patient_id"] != pid or previous["requested_by"] != user["id"]:
            raise HTTPException(409, "Эта форма уже использована")
        return previous["id"]
    current = assignment(c, pid)
    if current and not current["answered_at"]:
        return current["id"]
    ident = c.execute(
        "INSERT INTO medication_preference_requests(patient_id,requested_by,client_token,created_at) VALUES(?,?,?,?)",
        (pid, user["id"], token, now()),
    ).lastrowid
    audit(c, user["id"], "medication_preferences_assigned", pid, ident)
    return ident


def patient_context(c, path, user, pid):
    patient_scope(c, user, pid)
    row = c.execute(
        "SELECT * FROM medication_preference_versions WHERE patient_id=? ORDER BY revision DESC LIMIT 1",
        (pid,),
    ).fetchone()
    from app.services.adverse_reactions import preference_snapshot

    current_sent = preference_snapshot(c, pid, path)
    value = (
        decode(c, path, row)
        if row
        else dict(
            selected=current_sent["selected"],
            primary=current_sent["primary"],
            comment=current_sent.get("comment", ""),
            revision=0,
            status="initial",
        )
    )
    if row and row["status"] == "sent":
        value.update(
            selected=current_sent["selected"],
            primary=current_sent["primary"],
            comment=current_sent.get("comment", ""),
        )
    return dict(
        preference_assignment=assignment(c, pid),
        medication_preferences=value,
        preference_options=sorted(
            ELIGIBLE.values(), key=lambda v: (v["label"].casefold(), v["code"])
        ),
        preference_systems=sorted({o["system"] for o in OPTIONS}),
        preference_sent=current_sent,
    )


def save(c, path, user, pid, form):
    patient_scope(c, user, pid)
    requested = assignment(c, pid)
    if not requested:
        raise HTTPException(
            403, "Анкета пожеланий станет доступна после назначения специалистом"
        )
    intent = care_orders.field(form, "intent", 10)
    if intent not in ("draft", "send"):
        raise HTTPException(422, "Выберите сохранение черновика или отправку пожеланий")
    selected = form.getlist("effect")
    primary = care_orders.field(form, "primary", 20) or None
    comment = care_orders.field(form, "comment", 2000)
    if (
        len(selected) > 3
        or len(set(selected)) != len(selected)
        or any(code not in ELIGIBLE for code in selected)
    ):
        raise HTTPException(
            422, "Выберите не больше трёх эффектов из списка без повторов"
        )
    if primary and primary not in selected:
        raise HTTPException(422, "Главный эффект должен быть одним из выбранных")
    if selected and not primary and intent == "send":
        raise HTTPException(422, "Отметьте один основной эффект из выбранных")
    try:
        revision = int(care_orders.field(form, "revision", 20))
        if revision < 0:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Обновите страницу пожеланий") from None
    row = c.execute(
        "SELECT * FROM medication_preference_versions WHERE patient_id=? ORDER BY revision DESC LIMIT 1",
        (pid,),
    ).fetchone()
    payload = dict(
        selected=sorted(selected),
        primary=primary,
        comment=comment,
        catalog_version=CATALOG["version"],
    )
    status = "sent" if intent == "send" else "draft"
    current = decode(c, path, row) if row else None
    if (
        current
        and revision in (current["revision"], current["revision"] - 1)
        and status == current["status"]
        and all(current[k] == v for k, v in payload.items())
    ):
        # Replaying the prior POST is idempotent. A fresh explicit send creates
        # a new version even if the content is unchanged, so it can supersede
        # a newer intake response in the doctor's current context.
        if revision == current["revision"] - 1 or status == "draft":
            return current["revision"]
    if revision != (row["revision"] if row else 0):
        raise HTTPException(
            409,
            "Пожелания изменены в другой вкладке. Обновите страницу; прежние ответы сохранены.",
        )
    encrypted = files.cipher(path, c).encrypt(
        json.dumps(payload, ensure_ascii=False).encode()
    )
    c.execute(
        "INSERT INTO medication_preference_versions VALUES(?,?,?,?,?,?)",
        (pid, revision + 1, user["id"], status, encrypted, now()),
    )
    audit(
        c,
        user["id"],
        (
            "medication_preferences_sent"
            if status == "sent"
            else "medication_preferences_draft"
        ),
        pid,
        revision + 1,
    )
    if status == "sent" and not requested["answered_at"]:
        c.execute(
            "UPDATE medication_preference_requests SET answered_at=?,answer_revision=? WHERE id=? AND answered_at IS NULL",
            (now(), revision + 1, requested["id"]),
        )
    return revision + 1
