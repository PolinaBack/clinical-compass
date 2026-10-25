"""Versioned clinician-requested card analysis. All input comes from that card."""

from datetime import datetime, timedelta, timezone
import json
import threading

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from app.storage import connect, default_path, now, audit
from app.experimental_ai.ollama import Ollama, ERRORS
from app.experimental_ai.scenarios import canonical, digest
from app.experimental_ai.sources import retrieve
from app.experimental_ai.store import ExperimentError
from app.services.clinical_attachments import cipher
from app.services.patient_ai_data import collect, scope
from app.experimental_ai.free_text import MODES

MESSAGES = {
    **ERRORS,
    "no_data": "В карте пока нет сведений для анализа. Сохраните профиль, дневник, заметку или ответы анкеты.",
    "permission_changed": "Доступ врача или участие пациента изменились. Обработка остановлена.",
    "card_changed": "Данные карты изменились после запуска. Создайте новый анализ по актуальной версии.",
    "snapshot_unavailable": "Не удалось прочитать защищённый снимок. Проверьте наличие исходного ключа резервной копии.",
}


def expire(c):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(
        timespec="seconds"
    )
    c.execute(
        "UPDATE patient_ai_runs SET status='error',stage='error',error_code='worker_interrupted',finished_at=? WHERE status='running' AND heartbeat_at<?",
        (now(), cutoff),
    )


def enqueue(c, path, user, pid, *, response_mode="structured"):
    if response_mode not in MODES:
        raise HTTPException(422, "Выберите формат ответа модели")
    c.execute("BEGIN IMMEDIATE")
    data = collect(c, user["id"], pid, user["auth_version"])
    if not data["has_data"]:
        raise HTTPException(422, MESSAGES["no_data"])
    expire(c)
    checksum = digest(data)
    active = c.execute(
        "SELECT id,snapshot_hash,provenance FROM patient_ai_runs WHERE patient_id=? AND status IN ('queued','running')",
        (pid,),
    ).fetchone()
    same_mode = (
        active
        and json.loads(active["provenance"] or "{}").get("response_mode", "structured")
        == response_mode
    )
    if active and active["snapshot_hash"] == checksum and same_mode:
        return active["id"]
    if active:
        c.execute(
            "UPDATE patient_ai_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=?",
            (
                (
                    "card_changed"
                    if active["snapshot_hash"] != checksum
                    else "response_mode_changed"
                ),
                now(),
                active["id"],
            ),
        )
    encrypted = cipher(path, c).encrypt(canonical(data).encode())
    ident = c.execute(
        "INSERT INTO patient_ai_runs(patient_id,actor_id,actor_auth_version,status,stage,snapshot_hash,snapshot,provenance,created_at) VALUES(?,?,?,'queued','queued',?,?,?,?)",
        (
            pid,
            user["id"],
            user["auth_version"],
            checksum,
            encrypted,
            canonical(dict(response_mode=response_mode)),
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "patient_ai_requested", pid, ident)
    return ident


def decoded(c, path, row):
    if row is None:
        return None
    value = dict(row)
    try:
        key = cipher(path, c)
        value["snapshot"] = json.loads(key.decrypt(value["snapshot"]))
        value["result"] = (
            json.loads(key.decrypt(value["result"])) if value["result"] else None
        )
        value["provenance"] = (
            json.loads(value["provenance"]) if value["provenance"] else None
        )
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(503, MESSAGES["snapshot_unavailable"]) from None
    return value


def process_one(path=None, provider=None):
    path = path or default_path()
    with connect(path) as c:
        if not c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='patient_ai_runs'"
        ).fetchone():
            return False
        c.execute("BEGIN IMMEDIATE")
        expire(c)
        row = c.execute(
            "SELECT * FROM patient_ai_runs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        ident = row["id"]
        try:
            scope(c, row["actor_id"], row["patient_id"], row["actor_auth_version"])
            job = decoded(c, path, row)
            data = collect(
                c, row["actor_id"], row["patient_id"], row["actor_auth_version"]
            )
            if (
                digest(job["snapshot"]) != row["snapshot_hash"]
                or digest(data) != row["snapshot_hash"]
            ):
                raise ExperimentError("card_changed")
        except (HTTPException, ExperimentError) as exc:
            code = (
                exc.code
                if isinstance(exc, ExperimentError)
                else (
                    "permission_changed"
                    if exc.status_code == 403
                    else "snapshot_unavailable"
                )
            )
            c.execute(
                "UPDATE patient_ai_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=?",
                (code, now(), ident),
            )
            return ident
        c.execute(
            "UPDATE patient_ai_runs SET status='running',stage='analysis',started_at=?,heartbeat_at=? WHERE id=?",
            (now(), now(), ident),
        )

    stop = threading.Event()

    def progress(stage):
        with connect(path) as c:
            scope(c, row["actor_id"], row["patient_id"], row["actor_auth_version"])
            active = c.execute(
                "SELECT status FROM patient_ai_runs WHERE id=?", (ident,)
            ).fetchone()
            if not active or active[0] != "running":
                raise ExperimentError("card_changed")
            c.execute(
                "UPDATE patient_ai_runs SET stage=?,heartbeat_at=? WHERE id=? AND status='running'",
                (stage, now(), ident),
            )

    def heartbeat():
        while not stop.wait(5):
            try:
                with connect(path) as c:
                    c.execute(
                        "UPDATE patient_ai_runs SET heartbeat_at=? WHERE id=? AND status='running'",
                        (now(), ident),
                    )
            except Exception:
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        verified = retrieve(data)
        response_mode = (job["provenance"] or {}).get("response_mode", "structured")
        options = dict(response_mode="free") if response_mode == "free" else {}
        result, meta = (provider or Ollama()).generate_analysis(
            data, verified, progress, mode="patient-card", **options
        )
        meta = {**meta, "response_mode": response_mode}
        with connect(path) as c:
            c.execute("BEGIN IMMEDIATE")
            scope(c, row["actor_id"], row["patient_id"], row["actor_auth_version"])
            if (
                digest(
                    collect(
                        c, row["actor_id"], row["patient_id"], row["actor_auth_version"]
                    )
                )
                != row["snapshot_hash"]
            ):
                raise ExperimentError("card_changed")
            c.execute(
                "UPDATE patient_ai_runs SET status='completed',stage='completed',result=?,provenance=?,finished_at=?,heartbeat_at=? WHERE id=? AND status='running'",
                (
                    cipher(path, c).encrypt(canonical(result).encode()),
                    canonical(
                        dict(
                            **meta,
                            snapshot_hash=row["snapshot_hash"],
                            collector_version=data["version"]
                        )
                    ),
                    now(),
                    now(),
                    ident,
                ),
            )
            audit(c, row["actor_id"], "patient_ai_completed", row["patient_id"], ident)
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, ExperimentError)
            else (
                "permission_changed"
                if isinstance(exc, HTTPException) and exc.status_code == 403
                else "internal_error"
            )
        )
        with connect(path) as c:
            c.execute(
                "UPDATE patient_ai_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=? AND status='running'",
                (code, now(), ident),
            )
    finally:
        stop.set()
        thread.join(timeout=1)
    return ident
