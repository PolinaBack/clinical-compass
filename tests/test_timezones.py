"""Exercise IANA timezone support without a system database, as on Windows."""

import os
from pathlib import Path
import subprocess
import sys


def test_intake_and_settings_without_system_timezone_database(tmp_path):
    script = r"""
from datetime import datetime, timedelta
from zoneinfo import TZPATH, ZoneInfo
from fastapi.testclient import TestClient
from app.main import create_app
from app.services.clinical_forms import today, local_datetime
from tests.helpers import login
from tests.test_intake import form, send
import sys

assert not TZPATH, "This check must not use Linux system timezone files"
assert datetime(2026, 10, 22, tzinfo=ZoneInfo("Asia/Vladivostok")).utcoffset() == timedelta(hours=10)
assert local_datetime("2026-10-22T21:30:00+00:00") == "23.10.2026 00:30 МСК"
assert today().isoformat()
with TestClient(create_app(sys.argv[1])) as client:
    login(client, "patient1")
    assert client.get("/patients/1/profile/about").status_code == 200
    response = client.post(
        "/patients/1/intake/sections/personal",
        data=form(client, a_preferred_name="Тест часового пояса", a_birth_date="1990-01-01"),
    )
    assert response.status_code == 200
    assert send(client).status_code == 200
    assert client.get("/patients/1/profile/about").status_code == 200
    assert client.get("/settings").status_code == 200
    login(client, "doctor")
    response = client.get("/patients/1/profile/about")
    assert response.status_code == 200 and "Тест часового пояса" in response.text
    assert client.get("/patients/1/profile/about/personal").status_code == 200
    assert client.get("/settings").status_code == 200
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "windows-timezones.db")],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONTZPATH": "", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        timeout=90,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
