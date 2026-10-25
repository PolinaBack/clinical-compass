"""Explicit doctor-authored examination requests; private medicine drafts stay private."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from app.storage import audit, now
from app.services import clinical_attachments as files

KINDS = {
    "lab": "Лабораторное исследование",
    "instrumental": "Инструментальное обследование",
    "consultation": "Консультация специалиста",
    "advice": "Дополнительная рекомендация",
}
CATALOG = {
    "lab": (
        "Общий анализ крови",
        "Биохимический анализ крови",
        "Общий анализ мочи",
        "ТТГ",
        "Т4 свободный",
        "Ферритин",
        "Витамин B12",
        "Глюкоза крови",
        "АЛТ и АСТ",
    ),
    "instrumental": (
        "ЭКГ",
        "МРТ головного мозга",
        "ЭЭГ",
        "УЗИ органов брюшной полости",
        "УЗИ щитовидной железы",
    ),
    "consultation": (
        "Терапевт",
        "Психиатр",
        "Психолог",
        "Невролог",
        "Эндокринолог",
        "Кардиолог",
    ),
    "advice": (),
}
DUE = {
    "next_visit": "До следующей консультации",
    "week": "В течение недели",
    "two_weeks": "В течение двух недель",
    "month": "В течение месяца",
    "date": "К определённой дате",
    "other": "Другой срок",
    "none": "Срок не указан",
}
STATUSES = {
    "assigned": "Назначено",
    "completed": "Отмечено выполненным",
    "cancelled": "Отменено специалистом",
}
FILE_LIMIT = 700 * 1024


def scope(c, user, pid, *, doctor=False):
    if not isinstance(pid, int) or not 1 <= pid < 2**63:
        raise HTTPException(422, "Некорректный номер карты")
    active = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (user["id"],)
    ).fetchone()
    patient = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if not patient:
        raise HTTPException(404, "Карта не найдена")
    if (
        not active
        or active["auth_version"] != user["auth_version"]
        or not (
            active["role"] == "doctor"
            and active["doctor_id"] == patient["doctor_id"]
            or not doctor
            and active["role"] == "patient"
            and active["patient_id"] == pid
        )
    ):
        raise HTTPException(403, "Нет доступа к назначениям этой карты")
    return patient


def field(form, name, limit=3000):
    values = form.getlist(name)
    value = str(values[0] if values else "").strip()
    if (
        len(values) > 1
        or len(value) > limit
        or any(ord(s) < 32 and s not in "\n\r\t" for s in value)
    ):
        raise HTTPException(422, "Проверьте поля назначения")
    return value


def parse(form):
    kind = field(form, "kind", 30)
    name = field(form, "name", 200)
    custom = field(form, "custom_name", 200)
    if kind not in KINDS or name not in (*CATALOG[kind], "other"):
        raise HTTPException(
            422, "Выберите тип и исследование из списка или свой вариант"
        )
    if name == "other":
        name = custom
    if not name:
        raise HTTPException(422, "Укажите исследование, услугу или специалиста")
    due = field(form, "due", 30)
    if due not in DUE:
        raise HTTPException(422, "Выберите срок выполнения")
    due_date = field(form, "due_date", 20) if due == "date" else ""
    due_text = field(form, "due_text", 200) if due == "other" else ""
    if due == "date":
        try:
            due_date = date.fromisoformat(due_date).isoformat()
        except ValueError:
            raise HTTPException(422, "Укажите корректную дату выполнения") from None
    if due == "other" and not due_text:
        raise HTTPException(422, "Уточните срок выполнения")
    return dict(
        kind=kind,
        name=name,
        due=due,
        due_date=due_date,
        due_text=due_text,
        purpose=field(form, "purpose"),
        preparation=field(form, "preparation"),
        comment=field(form, "comment"),
    )


def decode(c, path, value):
    try:
        return json.loads(files.cipher(path, c).decrypt(value))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503,
            "Не удалось открыть назначение прежним ключом. Восстановите защищённую резервную копию.",
        ) from None


def create(c, path, user, pid, form):
    scope(c, user, pid, doctor=True)
    payload = parse(form)
    token = field(form, "client_token", 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        raise HTTPException(422, "Обновите форму назначения")
    old = c.execute(
        "SELECT * FROM care_orders WHERE author_id=? AND client_token=?",
        (user["id"], token),
    ).fetchone()
    if old:
        if old["patient_id"] != pid or decode(c, path, old["payload"]) != payload:
            raise HTTPException(
                409, "Эта форма уже отправлена. Откройте новую форму назначения."
            )
        return old["id"]
    encrypted = files.cipher(path, c).encrypt(
        json.dumps(payload, ensure_ascii=False).encode()
    )
    timestamp = now()
    ident = c.execute(
        "INSERT INTO care_orders(patient_id,author_id,kind,payload,client_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (pid, user["id"], payload["kind"], encrypted, token, timestamp, timestamp),
    ).lastrowid
    audit(c, user["id"], "care_order_sent", pid, ident)
    return ident


def order(c, user, pid, oid):
    scope(c, user, pid)
    if not isinstance(oid, int) or not 1 <= oid < 2**63:
        raise HTTPException(422, "Некорректный номер назначения")
    row = c.execute(
        "SELECT * FROM care_orders WHERE id=? AND patient_id=?", (oid, pid)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Назначение не найдено")
    return row


def listing(c, path, user, pid):
    scope(c, user, pid)
    result = []
    for row in c.execute(
        "SELECT o.*,a.role,d.name author_name FROM care_orders o JOIN accounts a ON a.id=o.author_id LEFT JOIN doctors d ON d.id=a.doctor_id WHERE o.patient_id=? ORDER BY o.id DESC",
        (pid,),
    ):
        payload = decode(c, path, row["payload"])
        due = payload["due"]
        due_label = (
            "До " + date.fromisoformat(payload["due_date"]).strftime("%d.%m.%Y")
            if due == "date"
            else payload["due_text"] if due == "other" else DUE[due]
        )
        attached = [
            dict(r)
            for r in c.execute(
                "SELECT id,filename,mime,byte_size,owner_id,comment,scan_status,created_at FROM care_order_files WHERE order_id=? AND deleted_at IS NULL ORDER BY id",
                (row["id"],),
            )
        ]
        result.append(
            dict(
                id=row["id"],
                revision=row["revision"],
                status=row["status"],
                status_label=STATUSES[row["status"]],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                author_name=row["author_name"],
                kind_label=KINDS[row["kind"]],
                due_label=due_label,
                files=attached,
                **payload
            )
        )
    return result


def change_status(c, user, pid, oid, form):
    row = order(c, user, pid, oid)
    intent = field(form, "intent", 20)
    if (
        intent not in ("complete", "reopen", "cancel")
        or intent == "cancel"
        and user["role"] != "doctor"
    ):
        raise HTTPException(403, "Это действие недоступно")
    try:
        revision = int(field(form, "revision", 20))
    except ValueError:
        raise HTTPException(422, "Обновите страницу назначения") from None
    if row["revision"] != revision:
        raise HTTPException(409, "Назначение изменилось. Обновите страницу.")
    if row["status"] == "cancelled":
        raise HTTPException(409, "Назначение уже отменено специалистом")
    status = {"complete": "completed", "reopen": "assigned", "cancel": "cancelled"}[
        intent
    ]
    if status == "cancelled" and field(form, "confirmed", 10) != "yes":
        raise HTTPException(422, "Подтвердите отмену назначения")
    if row["status"] != status:
        c.execute(
            "UPDATE care_orders SET status=?,revision=revision+1,updated_at=? WHERE id=?",
            (status, now(), oid),
        )
        audit(c, user["id"], "care_order_" + intent, pid, oid)


def save_file(c, path, user, pid, oid, upload, content, form, validated):
    row = order(c, user, pid, oid)
    if row["status"] == "cancelled":
        raise HTTPException(409, "К отменённому назначению нельзя добавлять файлы")
    name = Path((upload.filename or "attachment").replace("\\", "/")).name
    if not 1 <= len(name) <= 180 or any(ord(s) < 32 for s in name):
        raise HTTPException(422, "Проверьте имя файла")
    mime, scan_status, engine = validated
    if (
        not content
        or len(content) > FILE_LIMIT
        or mime not in ("application/pdf", "image/png", "image/jpeg")
    ):
        raise HTTPException(422, "Нужен PDF, PNG или JPG до 700 КБ")
    comment = field(form, "file_comment", 2000)
    digest = hashlib.sha256(content).hexdigest()
    old = c.execute(
        "SELECT id FROM care_order_files WHERE order_id=? AND owner_id=? AND sha256=? AND filename=? AND comment=? AND deleted_at IS NULL",
        (oid, user["id"], digest, name, comment),
    ).fetchone()
    if old:
        return old["id"]
    if (
        c.execute(
            "SELECT count(*) FROM care_order_files WHERE order_id=? AND deleted_at IS NULL",
            (oid,),
        ).fetchone()[0]
        >= 10
    ):
        raise HTTPException(
            422, "К одному назначению можно прикрепить до десяти файлов"
        )
    encrypted = files.cipher(path, c).encrypt(content)
    ident = c.execute(
        "INSERT INTO care_order_files(order_id,patient_id,owner_id,filename,mime,byte_size,sha256,content,comment,scan_status,scan_engine,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            oid,
            pid,
            user["id"],
            name,
            mime,
            len(content),
            digest,
            encrypted,
            comment,
            scan_status,
            engine,
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "care_order_file_sent", pid, oid)
    return ident


def scoped_file(c, user, pid, oid, fid):
    order(c, user, pid, oid)
    if not isinstance(fid, int) or not 1 <= fid < 2**63:
        raise HTTPException(422, "Некорректный номер файла")
    row = c.execute(
        "SELECT * FROM care_order_files WHERE id=? AND order_id=? AND patient_id=? AND deleted_at IS NULL",
        (fid, oid, pid),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Файл не найден")
    return row
