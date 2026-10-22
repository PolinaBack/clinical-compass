"""Progress is real job metadata, never a timer pretending to be recognition."""

from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from app.services import meetings as service
from app.services.meeting_progress import heartbeat, job_status
from app.storage import connect, now
from tests.helpers import login
from tests.test_meetings import (
    meeting,
    grants,
    upload,
    recording_id,
    queue,
    SyntheticModels,
)


@pytest.fixture
def queued(client, meeting):
    mid, context, _ = meeting
    grants(client, context)
    assert upload(client, mid).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303
    with connect(client.app.state.db_path) as c:
        jid = c.execute("SELECT id FROM meeting_jobs").fetchone()[0]
    return mid, rid, jid


def test_progress_is_monotonic_and_only_saved_result_is_100_percent(client, queued):
    mid, rid, jid = queued

    class Reporting(SyntheticModels):
        def transcribe(self, path, checkpoint):
            checkpoint.progress(stage="transcribe", processed_seconds=0.4)
            state = client.get(f"/meetings/{mid}/jobs").json()["jobs"][0]
            assert state["percent"] == 40 and "0:00 из 0:01" in state["progress_text"]
            checkpoint.progress(stage="transcribe", processed_seconds=0.2)
            assert (
                client.get(f"/meetings/{mid}/jobs").json()["jobs"][0]["percent"] == 40
            )
            checkpoint.progress(stage="transcribe", processed_seconds=1)
            assert (
                client.get(f"/meetings/{mid}/jobs").json()["jobs"][0]["percent"] == 99
            )
            return super().transcribe(path, checkpoint)

    assert service.process_one(client.app.state.db_path, Reporting())
    response = client.get(f"/meetings/{mid}/jobs")
    assert response.headers["cache-control"] == "no-store"
    status = response.json()["jobs"][0]
    assert status["status"] == "done" and status["percent"] == 100
    assert status["stage"] == "done"
    assert "worker_token" not in response.text and "payload" not in response.text
    assert (
        "Синтетический разговор" not in response.text
        and "filename" not in response.text
    )
    login(client, "patient1")
    assert client.get(f"/meetings/{mid}/jobs").status_code == 403
    assert "data-meeting-job" not in client.get(f"/meetings/{mid}").text
    login(client, "patient2")
    assert client.get(f"/meetings/{mid}/jobs").status_code == 403
    client.cookies.clear()
    assert client.get(f"/meetings/{mid}/jobs").status_code == 401


def test_heartbeat_and_advancing_recognition_are_separate_signals(client, queued):
    mid, _, jid = queued
    current = datetime.now(timezone.utc)
    old = (current - timedelta(minutes=5)).isoformat()
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE meeting_jobs SET status='running',stage='transcribe',started_at=?,heartbeat_at=?,progress_at=? WHERE id=?",
            (old, current.isoformat(), old, jid),
        )
        row = c.execute("SELECT * FROM meeting_jobs WHERE id=?", (jid,)).fetchone()
        live = job_status(row, 100, current)
        assert "на связи" in live["activity_text"]
        assert "Прогресс не менялся" in live["warning"]
        assert live["percent"] == 0  # elapsed time never advances recognition
        c.execute("UPDATE meeting_jobs SET heartbeat_at=? WHERE id=?", (old, jid))
        row = c.execute("SELECT * FROM meeting_jobs WHERE id=?", (jid,)).fetchone()
        assert "давно не отвечает" in job_status(row, 100, current)["warning"]
        c.execute("UPDATE meeting_jobs SET heartbeat_at=NULL WHERE id=?", (jid,))
        row = c.execute("SELECT * FROM meeting_jobs WHERE id=?", (jid,)).fetchone()
        assert "Нет данных об активности" in job_status(row, 100, current)["warning"]


def test_heartbeat_renews_lease_without_changing_progress_and_stops(client, queued):
    _, _, jid = queued
    path = client.app.state.db_path
    with connect(path) as c:
        c.execute(
            "UPDATE meeting_jobs SET status='running',worker_token='test-token',heartbeat_at='2000-01-01',progress_at='2000-01-01',lease_until='2000-01-01' WHERE id=?",
            (jid,),
        )
    with heartbeat(path, jid, "test-token", interval=0.02):
        deadline = time.monotonic() + 3
        while True:
            with connect(path) as c:
                row = c.execute(
                    "SELECT * FROM meeting_jobs WHERE id=?", (jid,)
                ).fetchone()
            if row["heartbeat_at"] != "2000-01-01":
                break
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        assert row["lease_until"] > now()
        assert row["progress_at"] == "2000-01-01" and row["processed_seconds"] == 0
        assert not service.process_one(
            path, SyntheticModels()
        )  # a second worker cannot steal it
    assert not any(
        t.name == "meeting-heartbeat" and t.is_alive() for t in threading.enumerate()
    )


def test_failed_attempt_keeps_progress_then_explicit_retry_resets_it(client, queued):
    mid, rid, jid = queued

    class Broken(SyntheticModels):
        def transcribe(self, path, checkpoint):
            checkpoint.progress(stage="transcribe", processed_seconds=0.6)
            raise RuntimeError("PRIVATE RECORD CONTENT")

    service.process_one(client.app.state.db_path, Broken())
    first = client.get(f"/meetings/{mid}/jobs").json()["jobs"][0]
    assert first["status"] == "failed" and first["percent"] == 60
    assert queue(client, rid, "transcript").status_code == 303
    retried = client.get(f"/meetings/{mid}/jobs").json()["jobs"][0]
    assert retried["id"] == jid and retried["status"] == "queued"
    assert retried["processed_seconds"] == 0 and retried["heartbeat_at"] is None


def test_terminal_elapsed_time_is_frozen(client, queued):
    _, _, jid = queued
    current = datetime.now(timezone.utc)
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE meeting_jobs SET status='done',stage='done',started_at=?,updated_at=? WHERE id=?",
            (
                (current - timedelta(minutes=2)).isoformat(),
                (current - timedelta(minutes=1)).isoformat(),
                jid,
            ),
        )
        row = c.execute("SELECT * FROM meeting_jobs WHERE id=?", (jid,)).fetchone()
        first = job_status(row, 1, current)
        later = job_status(row, 1, current + timedelta(hours=1))
    assert first["elapsed_text"] == later["elapsed_text"] == "Прошло: 1:00"
