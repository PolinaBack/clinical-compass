"""Private job metadata only. A heartbeat is not evidence of ASR progress."""

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from threading import Event, Thread

from app.storage import connect, now

STAGES = {
    "queued": "Ожидает обработчика",
    "prepare": "Подготовка задачи",
    "decode_audio": "Извлечение звука",
    "checking_model": "Проверка файлов модели",
    "loading_model": "Загрузка модели в память",
    "detecting_speech": "Чтение звука и поиск речи",
    "transcribe": "Распознавание речи",
    "validate_transcript": "Проверка расшифровки",
    "summarize": "Выделение главного из частей встречи",
    "condense_summary": "Объединение тем и повторов",
    "finalize_summary": "Краткий итог всей встречи",
    "validate_summary": "Проверка итогов",
    "save_result": "Сохранение результата",
    "done": "Готово",
}
STATUSES = {
    "queued": "ожидает обработчика",
    "running": "выполняется",
    "done": "завершена",
    "failed": "ошибка",
    "cancelled": "отменена",
}


def age(value, current):
    if not value:
        return None
    return max(
        0,
        int(
            (
                current - datetime.fromisoformat(value).astimezone(timezone.utc)
            ).total_seconds()
        ),
    )


def duration_label(seconds):
    seconds = max(0, int(seconds))
    return (
        f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
        if seconds >= 3600
        else f"{seconds // 60}:{seconds % 60:02d}"
    )


def job_status(row, duration, current=None):
    """Explicit allowlist: never return worker tokens, transcript or filenames."""
    current = current or datetime.now(timezone.utc)
    state = row["status"]
    processed = min(max(0, row["processed_seconds"]), duration)
    percent = None
    text = "Процент пока неизвестен: идёт подготовка."
    if state == "done":
        percent, text = 100, "Результат сохранён."
    elif row["kind"] == "transcript" and row["stage"] in (
        "transcribe",
        "validate_transcript",
        "save_result",
    ):
        percent = min(99, int(processed / duration * 100)) if duration else 0
        text = f"Распознано до {duration_label(processed)} из {duration_label(duration)} · {percent}%"
    elif row["kind"] == "summary" and row["units_total"]:
        percent = min(99, int(row["units_done"] / row["units_total"] * 100))
        text = (
            f"Готово частей: {row['units_done']} из {row['units_total']} · {percent}%"
        )
    heartbeat_age = age(row["heartbeat_at"], current)
    progress_age = age(row["progress_at"], current)
    elapsed = age(
        row["started_at"],
        (
            current
            if state in ("queued", "running")
            else datetime.fromisoformat(row["updated_at"]).astimezone(timezone.utc)
        ),
    )
    warning = ""
    activity = ""
    if state == "running":
        if heartbeat_age is None:
            warning = "Нет данных об активности. Возможно, задачу выполняет прежняя версия обработчика."
        elif heartbeat_age > 30:
            warning = "Обработчик давно не отвечает. Проверьте Terminal; выполнение могло остановиться."
        else:
            activity = f"Обработчик на связи · подтверждение {heartbeat_age} с назад."
        if progress_age is not None and progress_age >= 120:
            warning += (
                (" " if warning else "")
                + f"Прогресс не менялся {duration_label(progress_age)}. Это повод проверить обработчик, но не доказательство зависания."
            )
    return dict(
        id=row["id"],
        kind=row["kind"],
        status=state,
        status_label=STATUSES[state],
        stage=row["stage"],
        stage_label=STAGES.get(row["stage"], "Этап пока не сообщён"),
        percent=percent,
        progress_text=text,
        processed_seconds=processed,
        units_done=row["units_done"],
        units_total=row["units_total"],
        heartbeat_at=row["heartbeat_at"],
        progress_at=row["progress_at"],
        heartbeat_age_seconds=heartbeat_age,
        progress_age_seconds=progress_age,
        elapsed_text=(
            f"Прошло: {duration_label(elapsed)}" if elapsed is not None else ""
        ),
        activity_text=activity,
        warning=warning,
        error_code=row["error_code"],
    )


@contextmanager
def heartbeat(db_path, job_id, token, interval=5):
    """Keep the lease alive independently of long native calls, not the progress."""
    stopped = Event()

    def beat():
        while not stopped.wait(interval):
            try:
                with connect(db_path) as c:
                    stamp = now()
                    c.execute(
                        """UPDATE meeting_jobs SET heartbeat_at=?,lease_until=strftime('%Y-%m-%dT%H:%M:%S+00:00','now','+10 minutes')
                           WHERE id=? AND worker_token=? AND status='running'""",
                        (stamp, job_id, token),
                    )
            except Exception:
                # No exception text: it could contain private database paths.
                logging.getLogger(__name__).warning(
                    "Meeting heartbeat unavailable: job=%s", job_id
                )

    thread = Thread(target=beat, name="meeting-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)
