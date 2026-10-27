"""Patient document requests and clinician replies, separate from issued documents."""

from copy import deepcopy
from datetime import date
import hashlib
from pathlib import Path
import secrets

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from app.services import (
    intake,
    patient_extracts as documents,
    clinical_attachments as files,
)
from app.services.clinical_forms import today
from app.services.forum_attachments import validate as validate_document
from app.storage import audit, now

KINDS = {
    "certificate": "Справка",
    "extract": "Выписка",
    "conclusion": "Заключение",
    "prescription": "Рецепт (запрос)",
    "referral": "Направление",
    "copy": "Копия медицинской документации",
}
PURPOSES = {
    "education": "Для образовательного учреждения",
    "work": "Для работы",
    "required": "По месту требования",
    "specialist": "Для другого специалиста",
    "renewal": "Продление лечения / рецепта",
    "other": "Другое",
}
DUE = {
    "next_visit": "К следующей консультации",
    "date": "К определённой дате",
    "none": "Без даты",
}
STATES = {
    "received": "Отправлен специалисту",
    "in_progress": "В работе",
    "clarify": "Уточнить данные",
    "ready": "Готово",
    "declined": "Не может быть оформлен",
}
FIELDS = (
    "kind",
    "full_name",
    "birth_date",
    "representative",
    "contact",
    "purpose",
    "purpose_other",
    "due",
    "due_date",
    "comment",
)
DOCUMENT_FIELDS = ("kind", "purpose", "purpose_other", "due", "due_date")
MAX_DOCUMENTS = 50


def awaiting_reply_count(c, doctor_id):
    """Notify about patient input awaiting attention, not all unfinished documents."""
    return c.execute(
        """SELECT count(*) FROM extract_requests r
        JOIN patients p ON p.id=r.patient_id
        WHERE p.doctor_id=? AND r.status NOT IN ('ready','declined')
        AND COALESCE((SELECT e.actor_role FROM extract_request_events e
                      WHERE e.request_id=r.id ORDER BY e.id DESC LIMIT 1),
                     'patient')='patient'""",
        (doctor_id,),
    ).fetchone()[0]


def token(value):
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 100
        or not value.replace("-", "").replace("_", "").isalnum()
    ):
        raise HTTPException(422, "Обновите форму запроса и повторите отправку")
    return value


def prefill(c, user, pid):
    intake.check_scope(c, user, pid)
    if user["role"] != "patient":
        raise HTTPException(403, "Запрос отправляется из кабинета пациента")
    submitted = intake.read_submission(c, pid)
    saved = (
        intake.normalized_answers(submitted["data"].get("answers", {}))
        if submitted
        else {}
    )
    current = intake.draft(c, pid)["answers"]
    data = dict.fromkeys(FIELDS, "")
    for field in ("full_name", "birth_date", "contact"):
        for answers in (current, saved):
            value = answers.get("personal", {}).get(field, {})
            if value.get("status") == "answered" and value.get("value"):
                data[field] = str(value["value"])
                break
    if not data["full_name"]:
        data["full_name"] = c.execute(
            "SELECT name FROM patients WHERE id=?", (pid,)
        ).fetchone()[0]
    data.update(kind="certificate", purpose="required", due="next_visit", discuss=False)
    return data


def candidate(form):
    data = {key: str(form.get(key, "")).strip() for key in FIELDS}
    data["discuss"] = form.get("discuss") == "yes"
    return data


def requested_documents(form):
    """Older single-document forms remain valid; current forms carry numbered rows."""
    if "document_count" not in form:
        return [{key: str(form.get(key, "")).strip() for key in DOCUMENT_FIELDS}]
    try:
        count = int(form["document_count"])
        if not 1 <= count <= MAX_DOCUMENTS:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(
            422, "Добавьте хотя бы один документ; за раз можно заказать до 50"
        ) from None
    return [
        {
            key: str(form.get(f"doc_{index}_{key}", "")).strip()
            for key in DOCUMENT_FIELDS
        }
        for index in range(count)
    ]


def batch_form(form):
    data = candidate(form)
    data["documents"] = requested_documents(form)
    return data


def validate(data):
    if (
        data["kind"] not in KINDS
        or data["purpose"] not in PURPOSES
        or data["due"] not in DUE
    ):
        raise HTTPException(422, "Выберите вид документа, цель и срок запроса")
    if not data["full_name"]:
        raise HTTPException(422, "Укажите ФИО пациента в форме ниже")
    for field, future, label in [
        ("birth_date", False, "дату рождения"),
        ("due_date", True, "желаемую дату получения"),
    ]:
        if field == "due_date" and data["due"] != "date":
            data[field] = ""
            continue
        if not data[field] and field == "birth_date":
            continue
        try:
            value = date.fromisoformat(data[field])
            if (future and value < today()) or (not future and value > today()):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Проверьте " + label + " в форме ниже") from None
    return data


async def uploads(form):
    values = form.getlist("file")
    try:
        if len(values) > 1:
            raise HTTPException(422, "Прикрепите один файл к сообщению")
        result = []
        for upload in values:
            if not hasattr(upload, "read"):
                if upload == "":
                    continue
                raise HTTPException(422, "Выберите файл кнопкой прикрепления")
            if not upload.filename:
                continue
            name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if not name or len(name) > 180 or any(ord(ch) < 32 for ch in name):
                raise HTTPException(422, "Проверьте имя файла")
            if Path(name).suffix.casefold() not in {
                ".pdf",
                ".png",
                ".jpg",
                ".jpeg",
                ".txt",
            }:
                raise HTTPException(
                    422, "Прикрепите PDF, PNG, JPG или текстовый файл TXT"
                )
            content = await upload.read(files.file_limit() + 1)
            checked = await run_in_threadpool(validate_document, content, name)
            result.append((name, content, checked))
        return result
    finally:
        for upload in values:
            if hasattr(upload, "close"):
                await upload.close()


def save_files(c, path, user, rid, prepared, event_id=None):
    for name, content, (mime, state, engine) in prepared:
        encrypted = files.cipher(path, c).encrypt(content)
        c.execute(
            "INSERT INTO extract_request_files(request_id,event_id,owner_id,filename,mime,byte_size,sha256,content,scan_status,scan_engine,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                event_id,
                user["id"],
                name,
                mime,
                len(content),
                hashlib.sha256(content).hexdigest(),
                encrypted,
                state,
                engine,
                now(),
            ),
        )


def get(c, path, user, pid, rid):
    intake.check_scope(c, user, pid)
    row = c.execute(
        "SELECT * FROM extract_requests WHERE id=? AND patient_id=?", (rid, pid)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Запрос не найден")
    result = dict(row, data=documents.unpack(c, path, row["payload"]))
    result.pop("payload")
    result["events"] = [
        dict(r, data=documents.unpack(c, path, r["payload"]))
        for r in c.execute(
            "SELECT * FROM extract_request_events WHERE request_id=? ORDER BY id",
            (rid,),
        )
    ]
    for event in result["events"]:
        event.pop("payload")
    result["files"] = [
        dict(r)
        for r in c.execute(
            "SELECT f.id,f.request_id,f.event_id,f.filename,f.mime,f.byte_size,f.scan_status,f.created_at FROM extract_request_files f JOIN extract_requests r ON r.id=f.request_id WHERE r.patient_id=? AND (f.request_id=? OR (f.request_id=? AND f.event_id IS NULL)) ORDER BY f.id",
            (pid, rid, result["data"].get("shared_file_request", rid)),
        )
    ]
    result["published"] = False
    if row["document_id"]:
        linked = c.execute(
            "SELECT published_revision,author_id FROM patient_extracts WHERE id=? AND patient_id=?",
            (row["document_id"], pid),
        ).fetchone()
        result["published"] = bool(linked and linked["published_revision"])
        result["can_edit_document"] = bool(linked and linked["author_id"] == user["id"])
    return result


def listing(c, path, user, pid):
    intake.check_scope(c, user, pid)
    return [
        get(c, path, user, pid, r[0])
        for r in c.execute(
            "SELECT id FROM extract_requests WHERE patient_id=? ORDER BY updated_at DESC,id DESC",
            (pid,),
        )
    ]


def submit(c, path, user, pid, form, prepared):
    intake.check_scope(c, user, pid, write=True)
    nonce = token(str(form.get("client_token", "")))
    old = c.execute(
        "SELECT id,patient_id FROM extract_requests WHERE requested_by=? AND client_token=?",
        (user["id"], nonce),
    ).fetchone()
    if old:
        if old["patient_id"] != pid:
            raise HTTPException(409, "Эта форма относится к другому пациенту")
        return old["id"]
    data = validate(candidate(form))
    return store_request(c, path, user, pid, nonce, data, prepared)


def store_request(c, path, user, pid, nonce, data, prepared):
    encrypted = documents.pack(c, path, data)
    rid = c.execute(
        "INSERT INTO extract_requests(patient_id,requested_by,client_token,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (pid, user["id"], nonce, encrypted, now(), now()),
    ).lastrowid
    save_files(c, path, user, rid, prepared)
    audit(c, user["id"], "extract_requested", pid, rid)
    return rid


def submit_many(c, path, user, pid, form, prepared):
    intake.check_scope(c, user, pid, write=True)
    nonce = token(str(form.get("client_token", "")))
    old = c.execute(
        "SELECT id,patient_id,payload FROM extract_requests WHERE requested_by=? AND client_token=?",
        (user["id"], nonce),
    ).fetchone()
    if old:
        if old["patient_id"] != pid:
            raise HTTPException(409, "Эта форма относится к другому пациенту")
        total = documents.unpack(c, path, old["payload"]).get("batch_total", 1)
        ids = [old["id"]]
        for index in range(1, total):
            saved = c.execute(
                "SELECT id FROM extract_requests WHERE patient_id=? AND requested_by=? AND client_token=?",
                (pid, user["id"], batch_token(nonce, index)),
            ).fetchone()
            if not saved:
                raise HTTPException(
                    409,
                    "Не удалось восстановить отправленный заказ. Обновите страницу выписок",
                )
            ids.append(saved["id"])
        return ids
    parts = requested_documents(form)
    common = candidate(form)
    checked = []
    for index, part in enumerate(parts):
        try:
            checked.append(validate(dict(common, **part)))
        except HTTPException as exc:
            raise HTTPException(
                exc.status_code, f"Документ {index + 1}: {exc.detail}"
            ) from None
    ids = []
    for index, data in enumerate(checked):
        data.update(batch_total=len(checked), batch_position=index + 1)
        if ids and prepared:
            data["shared_file_request"] = ids[0]
        ids.append(
            store_request(
                c,
                path,
                user,
                pid,
                nonce if index == 0 else batch_token(nonce, index),
                data,
                prepared if index == 0 else [],
            )
        )
    return ids


def batch_token(nonce, index):
    return hashlib.sha256(f"{nonce}:{index}".encode()).hexdigest()


def event(c, path, user, pid, rid, status, text, nonce, prepared=()):
    encrypted = documents.pack(c, path, dict(text=text))
    eid = c.execute(
        "INSERT INTO extract_request_events(request_id,actor_id,actor_role,client_token,status,payload,created_at) VALUES(?,?,?,?,?,?,?)",
        (rid, user["id"], user["role"], nonce, status, encrypted, now()),
    ).lastrowid
    save_files(c, path, user, rid, prepared, eid)
    c.execute(
        "UPDATE extract_requests SET status=?,revision=revision+1,updated_at=? WHERE id=?",
        (status, now(), rid),
    )
    audit(c, user["id"], "extract_request_replied", pid, rid)


def reply(c, path, user, pid, rid, form, prepared):
    item = get(c, path, user, pid, rid)
    intake.check_scope(c, user, pid, write=user["role"] == "patient")
    nonce = token(str(form.get("client_token", "")))
    old = c.execute(
        "SELECT request_id FROM extract_request_events WHERE actor_id=? AND client_token=?",
        (user["id"], nonce),
    ).fetchone()
    if old:
        if old[0] != rid:
            raise HTTPException(409, "Обновите форму ответа")
        return
    if str(form.get("revision", "")) != str(item["revision"]):
        raise HTTPException(
            409,
            "Запрос уже обновлён. Ваш текст оставлен ниже; проверьте новый ответ и отправьте снова",
        )
    text = str(form.get("reply", "")).strip()
    if user["role"] == "doctor":
        status = str(form.get("status", ""))
        if status not in STATES:
            raise HTTPException(422, "Выберите статус запроса")
        if status in ("clarify", "declined", "ready") and not text and not prepared:
            raise HTTPException(422, "Добавьте ответ пациенту или прикрепите файл")
    else:
        if not text and not prepared:
            raise HTTPException(422, "Напишите уточнение или прикрепите файл")
        status = "received" if item["status"] == "clarify" else item["status"]
    event(c, path, user, pid, rid, status, text, nonce, prepared)


def make_draft(c, path, user, pid, rid, form):
    intake.check_scope(c, user, pid, doctor=True)
    item = get(c, path, user, pid, rid)
    if item["document_id"]:
        documents.read(c, path, user, pid, item["document_id"], edit=True)
        return item["document_id"]
    if str(form.get("revision", "")) != str(item["revision"]):
        raise HTTPException(
            409, "Запрос обновился. Проверьте сведения перед оформлением"
        )
    eid = documents.create(c, path, user, pid, f"extract_request_{rid}_{user['id']}")
    doc = documents.read(c, path, user, pid, eid, edit=True)
    data = deepcopy(doc["data"])
    original = item["data"]
    data["title"] = {
        "certificate": "Справка",
        "extract": "Выписка из медицинской карты",
        "conclusion": "Медицинское заключение",
        "prescription": "Ответ на запрос рецепта",
        "referral": "Направление",
        "copy": "Копия медицинской документации",
    }[original["kind"]]
    for key in ("full_name", "birth_date"):
        if original[key]:
            data["identity"][key] = original[key]
    data["recipient"] = (
        original["purpose_other"]
        if original["purpose"] == "other" and original["purpose_other"]
        else PURPOSES[original["purpose"]]
    )
    if original["purpose"] != "other" and original["purpose_other"]:
        data["recipient"] += ": " + original["purpose_other"]
    data["additional"] = (
        "Запрос пациента: " + KINDS[original["kind"]] + ". Цель: " + data["recipient"]
    )
    if original["comment"]:
        data["additional"] += "\nКомментарий пациента: " + original["comment"]
    if original["kind"] == "prescription":
        data["additional"] += "\nОтвет на запрос не является рецептурным бланком."
    data["reviewed"] = False
    documents.store_version(c, path, user, pid, eid, doc, data)
    c.execute("UPDATE extract_requests SET document_id=? WHERE id=?", (eid, rid))
    event(
        c,
        path,
        user,
        pid,
        rid,
        "in_progress",
        "Специалист начал оформление документа.",
        secrets.token_urlsafe(24),
    )
    return eid


def document_saved(c, path, user, pid, eid):
    item = c.execute(
        "SELECT id,status FROM extract_requests WHERE patient_id=? AND document_id=?",
        (pid, eid),
    ).fetchone()
    if item:
        event(
            c,
            path,
            user,
            pid,
            item["id"],
            (
                item["status"]
                if item["status"] in ("ready", "declined")
                else "in_progress"
            ),
            "Специалист сохранил данные документа.",
            secrets.token_urlsafe(24),
        )


def document_state(c, path, user, pid, eid, published):
    item = c.execute(
        "SELECT id FROM extract_requests WHERE patient_id=? AND document_id=?",
        (pid, eid),
    ).fetchone()
    if item:
        event(
            c,
            path,
            user,
            pid,
            item["id"],
            "ready" if published else "in_progress",
            (
                "Специалист опубликовал готовый документ."
                if published
                else "Публикация документа снята; оформление продолжается."
            ),
            secrets.token_urlsafe(24),
        )
