"""Real SQLite startup checks; never download models or change the working database."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.meeting_worker import main


@pytest.mark.parametrize("parent_exists", [True, False])
def test_missing_database_is_not_created(tmp_path, monkeypatch, capsys, parent_exists):
    parent = tmp_path / "База встреч #1"
    if parent_exists:
        parent.mkdir()
    path = parent / "compass.db"
    monkeypatch.setenv("COMPASS_DB", str(path))
    with pytest.raises(SystemExit) as error:
        main(["--once"])
    assert "Файл базы данных не найден" in str(error.value)
    assert str(path.resolve()) in str(error.value)
    assert "Application startup complete" in str(error.value)
    assert not path.exists()
    assert parent.exists() is parent_exists
    assert "запущен" not in capsys.readouterr().out


def test_legacy_database_is_preserved_and_gets_actionable_error(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE diary (id INTEGER PRIMARY KEY, answer TEXT)")
        c.execute("INSERT INTO diary VALUES (1, 'Сохранённая запись')")
    before = path.read_bytes()
    monkeypatch.setenv("COMPASS_DB", str(path))
    with pytest.raises(SystemExit) as error:
        main(["--once"])
    message = str(error.value)
    assert "миграции 005/006" in message
    assert "python -m uvicorn app.main:app --reload" in message
    assert "COMPASS_DB" in message
    assert path.read_bytes() == before


def test_unreadable_database_gets_clear_error(tmp_path, monkeypatch):
    path = tmp_path / "not-sqlite.db"
    path.write_bytes(b"not a SQLite database")
    monkeypatch.setenv("COMPASS_DB", str(path))
    with pytest.raises(SystemExit, match="Не удалось прочитать базу SQLite"):
        main(["--once"])
    assert path.read_bytes() == b"not a SQLite database"


def test_application_startup_prepares_worker_database(client, monkeypatch, capsys):
    # The real application lifespan must apply migration 005, even with no models.
    path = client.app.state.db_path
    monkeypatch.chdir(path.parent)
    monkeypatch.setenv("COMPASS_DB", path.name)
    main(["--once"])
    output = capsys.readouterr().out
    assert str(path.resolve()) in output
    assert "Обработчик локальных встреч запущен" in output
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM meeting_jobs").fetchone()[0] == 0


def test_cli_failure_has_no_traceback_or_false_start_message(tmp_path):
    path = tmp_path / "missing.db"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.meeting_worker", "--once"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "COMPASS_DB": str(path), "PYTHONUTF8": "1"},
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 1
    assert "Обработчик не запущен" in result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout
    assert not path.exists()
