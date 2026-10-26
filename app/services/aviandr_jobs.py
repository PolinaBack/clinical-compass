"""Durable, access-checked explanatory jobs; deterministic analysis survives errors."""

import threading
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from app.storage import connect, default_path, now, audit
from app.services.aviandr_analysis import build
from app.services.aviandr_ollama import AviandrOllama
from app.services.patient_ai import decoded
from app.services.patient_ai_data import scope
from app.services.medicine_reactions import pack
from app.experimental_ai.store import ExperimentError
from app.experimental_ai.scenarios import canonical, digest
from app.experimental_ai.ollama import ERRORS
from app.experimental_ai.free_text import MODES

MESSAGES = {
    **ERRORS,
    "unsafe_response": "В прежнем запуске текст не был сохранён. Запустите объяснение заново.",
    "card_changed": "Данные карты, проверенные правила или версия расчёта изменились. Запустите новое текстовое объяснение.",
    "permission_changed": "Доступ к карте изменился; обработка остановлена.",
    "input_too_large": "Контекст слишком большой для одного запроса. Проверяемый расчёт доступен; текстовая генерация не выполнена.",
}


def expire(c):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(
        timespec="seconds"
    )
    c.execute(
        "UPDATE aviandr_runs SET status='error',stage='error',error_code='worker_interrupted',finished_at=? WHERE status='running' AND heartbeat_at<?",
        (now(), cutoff),
    )


def enqueue(c, path, user, pid, *, response_mode="structured"):
    if response_mode not in MODES:
        raise HTTPException(422, "Выберите формат ответа модели")
    c.execute("BEGIN IMMEDIATE")
    report = build(c, path, user, pid)
    expire(c)
    row = c.execute(
        "SELECT * FROM aviandr_runs WHERE patient_id=? AND status IN ('queued','running')",
        (pid,),
    ).fetchone()
    same_mode = (
        row
        and json.loads(row["provenance"] or "{}").get("response_mode", "structured")
        == response_mode
    )
    if row and row["snapshot_hash"] == report["checksum"] and same_mode:
        return row["id"]
    if row:
        c.execute(
            "UPDATE aviandr_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=?",
            (
                (
                    "card_changed"
                    if row["snapshot_hash"] != report["checksum"]
                    else "response_mode_changed"
                ),
                now(),
                row["id"],
            ),
        )
    ident = c.execute(
        "INSERT INTO aviandr_runs(patient_id,actor_id,actor_auth_version,status,stage,snapshot_hash,snapshot,provenance,created_at) VALUES(?,?,?,'queued','queued',?,?,?,?)",
        (
            pid,
            user["id"],
            user["auth_version"],
            report["checksum"],
            pack(c, path, report),
            canonical(dict(response_mode=response_mode)),
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "aviandr_explanation_requested", pid, ident)
    return ident


def process_one(path=None, provider=None):
    path = path or default_path()
    with connect(path) as c:
        if not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='aviandr_runs'"
        ).fetchone():
            return False
        c.execute("BEGIN IMMEDIATE")
        expire(c)
        row = c.execute(
            "SELECT * FROM aviandr_runs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        row = dict(row)
        ident = row["id"]
        try:
            user, _ = scope(
                c, row["actor_id"], row["patient_id"], row["actor_auth_version"]
            )
            job = decoded(c, path, row)
            if (
                build(c, path, user, row["patient_id"])["checksum"]
                != row["snapshot_hash"]
                or digest({k: v for k, v in job["snapshot"].items() if k != "checksum"})
                != row["snapshot_hash"]
            ):
                raise ExperimentError("card_changed")
        except (HTTPException, ExperimentError) as exc:
            c.execute(
                "UPDATE aviandr_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=?",
                (
                    (
                        exc.code
                        if isinstance(exc, ExperimentError)
                        else "permission_changed"
                    ),
                    now(),
                    ident,
                ),
            )
            return ident
        c.execute(
            "UPDATE aviandr_runs SET status='running',stage='model',started_at=?,heartbeat_at=? WHERE id=?",
            (now(), now(), ident),
        )
    stop = threading.Event()

    def progress(stage):
        with connect(path) as c:
            scope(c, row["actor_id"], row["patient_id"], row["actor_auth_version"])
            updated = c.execute(
                "UPDATE aviandr_runs SET stage=?,heartbeat_at=? WHERE id=? AND status='running'",
                (stage, now(), ident),
            )
            if not updated.rowcount:
                raise ExperimentError("card_changed")

    def heartbeat():
        while not stop.wait(5):
            try:
                progress("generate")
            except Exception:
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        response_mode = (job["provenance"] or {}).get("response_mode", "structured")
        options = dict(response_mode="free") if response_mode == "free" else {}
        result, meta = (provider or AviandrOllama()).explain(
            job["snapshot"], progress, **options
        )
        meta = {**meta, "response_mode": response_mode}
        with connect(path) as c:
            c.execute("BEGIN IMMEDIATE")
            user, _ = scope(
                c, row["actor_id"], row["patient_id"], row["actor_auth_version"]
            )
            meta["input_changed_during_generation"] = (
                build(c, path, user, row["patient_id"])["checksum"]
                != row["snapshot_hash"]
            )
            c.execute(
                "UPDATE aviandr_runs SET status='completed',stage='completed',result=?,provenance=?,finished_at=?,heartbeat_at=? WHERE id=? AND status='running'",
                (pack(c, path, result), canonical(meta), now(), now(), ident),
            )
            audit(
                c, user["id"], "aviandr_explanation_completed", row["patient_id"], ident
            )
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
                "UPDATE aviandr_runs SET status='error',stage='error',error_code=?,finished_at=? WHERE id=? AND status='running'",
                (code, now(), ident),
            )
    finally:
        stop.set()
        thread.join(timeout=1)
    return ident
