"""Durable single-scenario experiments, with heartbeat and append-only evaluations."""

from datetime import datetime, timedelta, timezone
import json
import threading

from . import analysis, sources, store
from .ollama import Ollama
from .scenarios import SCENARIO, canonical, digest

STAGES = {
    "queued": "Ожидает отдельного обработчика",
    "analysis": "Программный анализ",
    "model": "Проверка локальной модели",
    "generate": "Ожидание генерации Ollama",
    "compare": "ИИ сопоставляет сведения пациента с инструкцией",
    "validate": "Проверка структуры и оснований",
    "read": "Получен текст модели без автоматической проверки",
    "completed": "Эксперимент завершён",
    "error": "Эксперимент не завершён",
}
ISSUES = {
    "none": "Замечаний не отмечено",
    "unsupported": "Недостаточно оснований в данных",
    "unclear": "Формулировки неясны",
    "safety": "Есть нежелательные утверждения",
    "sources": "Нужно проверить материалы",
}


def expire(c):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(
        timespec="seconds"
    )
    c.execute(
        "UPDATE experiment_runs SET status='error',stage='error',error_code='worker_interrupted',finished_at=? WHERE status='running' AND heartbeat_at<?",
        (store.now(), cutoff),
    )


def enqueue(path, actor_id, main_path=None):
    with store.connect(path, main_path=main_path) as c:
        c.execute("BEGIN IMMEDIATE")
        data = store.load(c)
        expire(c)
        active = c.execute(
            "SELECT id FROM experiment_runs WHERE status IN ('queued','running')"
        ).fetchone()
        if active:
            return active[0]
        return c.execute(
            "INSERT INTO experiment_runs(scenario_id,dataset_hash,actor_id,status,stage,created_at) VALUES(?,?,?,'queued','queued',?)",
            (SCENARIO, digest(data), actor_id, store.now()),
        ).lastrowid


def decoded(row):
    if not row:
        return None
    result = dict(row)
    for key in ("result", "metrics", "provenance"):
        result[key] = json.loads(result[key]) if result[key] else None
    return result


def process_one(path=None, provider=None):
    path = path or store.default_path()
    with store.connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        data = store.load(c)
        expire(c)
        row = c.execute(
            "SELECT * FROM experiment_runs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        job_id = row["id"]
        if row["dataset_hash"] != digest(data) or row["scenario_id"] != SCENARIO:
            raise store.ExperimentError("synthetic_integrity_failed")
        c.execute(
            "UPDATE experiment_runs SET status='running',stage='analysis',started_at=?,heartbeat_at=? WHERE id=?",
            (store.now(), store.now(), job_id),
        )

    stop = threading.Event()

    def progress(stage):
        with store.connect(path) as c:
            store.load(c)
            c.execute(
                "UPDATE experiment_runs SET stage=?,heartbeat_at=? WHERE id=? AND status='running'",
                (stage, store.now(), job_id),
            )

    def heartbeat():
        while not stop.wait(5):
            try:
                with store.connect(path) as c:
                    c.execute(
                        "UPDATE experiment_runs SET heartbeat_at=? WHERE id=? AND status='running'",
                        (store.now(), job_id),
                    )
            except store.ExperimentError:
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        metrics = analysis.analyze(data)
        verified = sources.retrieve(metrics)
        with store.connect(path) as c:
            c.execute(
                "UPDATE experiment_runs SET metrics=? WHERE id=?",
                (canonical(metrics), job_id),
            )
        result, metadata = (provider or Ollama()).generate(
            data, metrics, verified, progress
        )
        # Recheck the protected input after generation before storing any success.
        with store.connect(path) as c:
            store.load(c)
            c.execute(
                "UPDATE experiment_runs SET status='completed',stage='completed',finished_at=?,heartbeat_at=?,result=?,provenance=? WHERE id=? AND status='running'",
                (
                    store.now(),
                    store.now(),
                    canonical(result),
                    canonical(
                        dict(
                            dataset_hash=digest(data),
                            scenario=SCENARIO,
                            analysis_version=analysis.VERSION,
                            **metadata
                        )
                    ),
                    job_id,
                ),
            )
    except Exception as exc:
        code = exc.code if isinstance(exc, store.ExperimentError) else "internal_error"
        # No exception repr, input records or model response is logged/stored on failure.
        with store.connect(path) as c:
            c.execute(
                "UPDATE experiment_runs SET status='error',stage='error',finished_at=?,error_code=? WHERE id=? AND status='running'",
                (store.now(), code, job_id),
            )
    finally:
        stop.set()
        thread.join(timeout=1)
    return True


def review(c, run_id, actor_id, grounding, usefulness, safety, issue):
    row = c.execute(
        "SELECT status FROM experiment_runs WHERE id=?", (run_id,)
    ).fetchone()
    if (
        not row
        or row[0] != "completed"
        or any(str(x) not in {"0", "1", "2"} for x in (grounding, usefulness, safety))
        or issue not in ISSUES
    ):
        raise store.ExperimentError("invalid_review")
    c.execute(
        "INSERT INTO experiment_reviews(run_id,actor_id,grounding,usefulness,safety,issue,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            run_id,
            actor_id,
            int(grounding),
            int(usefulness),
            int(safety),
            issue,
            store.now(),
        ),
    )
