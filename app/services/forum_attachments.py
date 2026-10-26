"""Encrypted forum files, separate from patient records and scoped to publication."""

import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.services import clinical_attachments as files, clinician_forum
from app.storage import now

FIELDS = {
    "topic": "topic_id",
    "comment": "comment_id",
    "faq_question": "faq_id",
    "faq_answer": "faq_id",
}
MAX_FILES = 5


def ids(value):
    try:
        result = json.loads(value or "[]")
        if (
            not isinstance(result, list)
            or len(result) > MAX_FILES
            or any(type(v) is not int or v <= 0 for v in result)
            or len(set(result)) != len(result)
        ):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise HTTPException(422, "Обновите список прикреплённых файлов") from None


def listing(c, kind, target):
    return [
        dict(r)
        for r in c.execute(
            f"SELECT id,filename,mime,byte_size,scan_status FROM forum_attachments WHERE kind=? AND {FIELDS[kind]}=? ORDER BY id",
            (kind, target),
        )
    ]


def selected(c, values, actor, kind, target=None):
    result = []
    for ident in values:
        row = c.execute(
            "SELECT * FROM forum_attachments WHERE id=?", (ident,)
        ).fetchone()
        if (
            not row
            or row["owner_id"] != actor["id"]
            or row["kind"] != kind
            or row[FIELDS[kind]] not in (None, target)
        ):
            raise HTTPException(
                403, "Можно прикрепить только свои файлы к этой публикации"
            )
        result.append(
            dict(
                id=row["id"],
                filename=row["filename"],
                mime=row["mime"],
                byte_size=row["byte_size"],
                scan_status=row["scan_status"],
            )
        )
    return result


def validate(content, filename):
    suffix = Path(filename).suffix.casefold()
    if suffix == ".txt":
        if not content or len(content) > files.file_limit():
            raise HTTPException(422, "Файл пустой или превышает лимит")
        try:
            value = content.decode("utf-8-sig")
            if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value):
                raise ValueError()
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(
                422, "Текстовый файл должен быть в UTF-8 без служебных символов"
            ) from None
        state, engine = files.scan(content, suffix)
        if state == "infected":
            raise HTTPException(422, "Антивирус обнаружил угрозу; файл не сохранён")
        return "text/plain", state, engine
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(422, "На форуме можно прикрепить PDF, PNG, JPG или TXT")
    return files.validate(content, filename)


async def read_uploads(form):
    uploads = form.getlist("file")
    if len(uploads) > MAX_FILES:
        raise HTTPException(422, "Можно прикрепить до пяти файлов")
    prepared = []
    try:
        for upload in uploads:
            if not hasattr(upload, "read"):
                if upload == "":
                    continue
                raise HTTPException(422, "Выберите файл кнопкой прикрепления")
            if not upload.filename:
                continue
            filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if (
                not filename
                or len(filename) > 180
                or any(ord(ch) < 32 for ch in filename)
            ):
                raise HTTPException(422, "Сократите или исправьте имя файла")
            content = await upload.read(files.file_limit() + 1)
            validation = await run_in_threadpool(validate, content, filename)
            prepared.append((filename, content, validation))
        return prepared
    finally:
        for upload in uploads:
            if hasattr(upload, "close"):
                await upload.close()


def save(c, path, kind, prepared, owner=None, target=None):
    if len(prepared) > MAX_FILES:
        raise HTTPException(422, "Можно прикрепить до пяти файлов")
    result = []
    for filename, content, (mime, state, engine) in prepared:
        values = dict(topic_id=None, comment_id=None, faq_id=None)
        values[FIELDS[kind]] = target
        ident = c.execute(
            "INSERT INTO forum_attachments(owner_id,kind,topic_id,comment_id,faq_id,filename,mime,byte_size,sha256,content,scan_status,scan_engine,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner,
                kind,
                values["topic_id"],
                values["comment_id"],
                values["faq_id"],
                filename,
                mime,
                len(content),
                hashlib.sha256(content).hexdigest(),
                files.cipher(path, c).encrypt(content),
                state,
                engine,
                now(),
            ),
        ).lastrowid
        result.append(ident)
    return result


def bind(c, values, actor, kind, target):
    selected(c, values, actor, kind, target)
    for ident in values:
        c.execute(
            f"UPDATE forum_attachments SET {FIELDS[kind]}=? WHERE id=?", (target, ident)
        )


def scoped(c, ident, actor):
    row = c.execute("SELECT * FROM forum_attachments WHERE id=?", (ident,)).fetchone()
    if not row:
        raise HTTPException(404, "Файл не найден")
    if row["kind"].startswith("faq_"):
        question = c.execute(
            "SELECT status FROM forum_questions WHERE id=?", (row["faq_id"],)
        ).fetchone()
        if (
            not question
            or question["status"] != "published"
            and (not actor or actor["role"] != "doctor")
        ):
            raise HTTPException(404, "Файл пока не опубликован или скрыт")
    else:
        if not actor or actor["role"] != "doctor":
            raise HTTPException(403, "Файл доступен только врачам")
        if row["topic_id"] is not None:
            clinician_forum.topic(c, row["topic_id"], actor)
        elif row["comment_id"] is not None:
            item = c.execute(
                "SELECT * FROM clinician_comments WHERE id=? AND status!='deleted'",
                (row["comment_id"],),
            ).fetchone()
            if (
                not item
                or item["status"] == "hidden"
                and item["author_id"] != actor["id"]
            ):
                raise HTTPException(404, "Ответ недоступен")
            clinician_forum.topic(c, item["topic_id"], actor)
        elif row["owner_id"] != actor["id"]:
            raise HTTPException(403, "Черновой файл доступен только автору")
    return row


def decrypt(c, path, row):
    try:
        value = files.cipher(path, c).decrypt(row["content"])
        if hashlib.sha256(value).hexdigest() != row["sha256"]:
            raise ValueError()
        return value
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Не удалось открыть файл прежним ключом") from None
