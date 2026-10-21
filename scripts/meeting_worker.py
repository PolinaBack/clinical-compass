"""Local persistent queue. Start separately from uvicorn; no implicit model download."""

import argparse
import sqlite3
import time
from contextlib import closing

from app.storage import default_path
from app.services.meetings import process_one


def checked_database_path():
    """Inspect the existing database without creating a file or applying migrations."""
    path = default_path().resolve()

    def stop(reason):
        raise SystemExit(
            f"Обработчик не запущен.\nБаза данных: {path}\n{reason}\n"
            "Сначала запустите или перезапустите обновлённое приложение: "
            "python -m uvicorn app.main:app --reload\n"
            "Дождитесь Application startup complete, затем повторите запуск обработчика "
            "во втором Terminal. COMPASS_DB должен указывать на одну и ту же базу "
            "у приложения и обработчика. Не удаляйте существующую SQLite.\n"
            "Подробности: docs/MEETING_AI.md"
        )

    if not path.is_file():
        stop("Файл базы данных не найден.")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as c:
            tables = {
                row[0]
                for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "meeting_sessions",
                "meeting_recordings",
                "meeting_jobs",
                "meeting_artifacts",
                "meeting_events",
                "schema_migrations",
            }
            ready = (
                required <= tables
                and c.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?",
                    ("005_meeting_processing.sql",),
                ).fetchone()
            )
    except sqlite3.Error:
        stop(
            "Не удалось прочитать базу SQLite. Проверьте выбранный файл и права доступа."
        )
    if not ready:
        stop("В этой базе ещё не применена миграция 005 для записей встреч.")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued task and exit"
    )
    args = parser.parse_args(argv)
    path = checked_database_path()
    print(f"База данных обработчика: {path}")
    print(
        "Обработчик локальных встреч запущен. Ctrl+C — остановить. Содержимое записей в журнал не выводится."
    )
    try:
        while True:
            worked = process_one(path)
            if args.once:
                break
            if not worked:
                time.sleep(2)
    except KeyboardInterrupt:
        print(
            "Обработчик остановлен. Незавершённые задачи восстановятся по сроку аренды; повтор запускает врач."
        )


if __name__ == "__main__":
    main()
