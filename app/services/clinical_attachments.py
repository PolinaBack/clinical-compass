"""Private encrypted attachments; no public URLs, remote ASR or invented virus scan."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from fastapi import HTTPException
from app.services.meeting_media import inspect_media
from app.storage import now


def file_limit():
    try:
        n = int(os.getenv("COMPASS_ATTACHMENT_MAX_MB", "10"))
    except ValueError:
        raise HTTPException(503, "Некорректный лимит вложений") from None
    if not 1 <= n <= 25:
        raise HTTPException(503, "Лимит вложений должен быть от 1 до 25 МБ")
    return n * 1024 * 1024


def key_path(db_path):
    sidecar = Path(str(db_path) + ".key")
    return Path(
        os.getenv(
            "COMPASS_ATTACHMENT_KEY_FILE",
            str(
                sidecar
                if sidecar.exists()
                else Path(db_path).parent / "attachments.key"
            ),
        )
    )


def cipher(db_path, c):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise HTTPException(
            503, "Для вложений установите обновлённый requirements.txt"
        ) from None
    key_file = key_path(db_path)
    if not key_file.exists():
        patient_ai = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='patient_ai_runs'"
        ).fetchone()
        medicine_drafts = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='medicine_drafts'"
        ).fetchone()
        if (
            (
                patient_ai
                and c.execute("SELECT 1 FROM patient_ai_runs LIMIT 1").fetchone()
            )
            or (
                medicine_drafts
                and c.execute("SELECT 1 FROM medicine_drafts LIMIT 1").fetchone()
            )
            or c.execute(
                "SELECT 1 FROM clinical_attachments WHERE content IS NOT NULL LIMIT 1"
            ).fetchone()
        ):
            raise HTTPException(
                503,
                "Не найден прежний ключ вложений. Восстановите ключ из защищённой резервной копии; новый ключ не создаётся.",
            )
        key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            pass
    try:
        return Fernet(key_file.read_bytes())
    except (OSError, ValueError):
        raise HTTPException(503, "Не удалось прочитать ключ вложений") from None


def scan(content, suffix):
    executable = shutil.which("clamdscan") or shutil.which("clamscan")
    if not executable:
        return "not_scanned", ""
    with tempfile.TemporaryDirectory(prefix="compass-file-scan-") as folder:
        path = Path(folder) / ("attachment" + suffix)
        path.write_bytes(content)
        os.chmod(path, 0o600)
        try:
            p = subprocess.run(
                [executable, "--no-summary", str(path)],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "error", "ClamAV"
        return (
            "clean"
            if p.returncode == 0
            else "infected" if p.returncode == 1 else "error"
        ), "ClamAV"


def validate(content, filename):
    if not content or len(content) > file_limit():
        raise HTTPException(422, "Файл пустой или превышает лимит вложений")
    suffix = Path(filename).suffix.casefold()
    if (
        suffix == ".pdf"
        and content.startswith(b"%PDF-")
        and b"%%EOF" in content[-2048:]
    ):
        if any(
            t in content
            for t in (
                b"/JavaScript",
                b"/JS",
                b"/Launch",
                b"/EmbeddedFile",
                b"/RichMedia",
            )
        ):
            raise HTTPException(
                422,
                "PDF с активным содержимым или вложенными файлами не поддерживается",
            )
        mime = "application/pdf"
    elif (
        suffix == ".png"
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content.endswith(b"IEND\xaeB`\x82")
    ):
        mime = "image/png"
    elif (
        suffix in (".jpg", ".jpeg")
        and content.startswith(b"\xff\xd8")
        and content.endswith(b"\xff\xd9")
    ):
        mime = "image/jpeg"
    elif suffix in (".wav", ".mp3", ".ogg", ".webm", ".m4a", ".flac"):
        info = inspect_media(content)
        if not info["mime"].startswith("audio/") or info["duration"] > 180:
            raise HTTPException(
                422, "Для голосовой заметки нужно только аудио до 3 минут"
            )
        mime = info["mime"]
    else:
        raise HTTPException(
            422,
            "Нужен PDF, JPG, PNG или короткая аудиозаметка; расширение должно соответствовать содержимому",
        )
    state, engine = scan(content, suffix)
    if state == "infected":
        raise HTTPException(422, "Антивирус обнаружил угрозу; файл не сохранён")
    return mime, state, engine


def save(
    c,
    db_path,
    pid,
    user,
    scope,
    category,
    entity_id,
    upload,
    content,
    form,
    *,
    validated=None
):
    name = Path((upload.filename or "attachment").replace("\\", "/")).name
    if not 1 <= len(name) <= 180 or any(ord(t) < 32 for t in name):
        raise HTTPException(422, "Проверьте имя файла")
    mime, state, engine = validated or validate(content, name)
    comment = str(form.get("file_comment", "")).strip()
    study_date = str(form.get("study_date", "")).strip()
    if len(comment) > 2000 or len(study_date) > 100:
        raise HTTPException(422, "Слишком длинные сведения о файле")
    encrypted = cipher(db_path, c).encrypt(content)
    ident = c.execute(
        "INSERT INTO clinical_attachments(patient_id,owner_id,scope,category,entity_id,filename,mime,byte_size,sha256,content,study_date,comment,sensitive,scan_status,scan_engine,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid,
            user["id"],
            scope,
            category,
            entity_id,
            name,
            mime,
            len(content),
            hashlib.sha256(content).hexdigest(),
            encrypted,
            study_date,
            comment,
            int(form.get("sensitive") == "yes"),
            state,
            engine,
            now(),
        ),
    ).lastrowid
    return ident


def decrypt(c, db_path, row):
    try:
        return cipher(db_path, c).decrypt(row["content"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503, "Не удалось открыть вложение прежним ключом; содержимое не изменено"
        ) from None
