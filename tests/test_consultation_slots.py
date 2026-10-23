"""Actual availability, conflicting requests, durable cancellation and unified profile."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, now
from app.services.consultations import book
from tests.helpers import login, token


def post(client, path, **data):
    return client.post(path, data=dict(csrf=token(client), **data))


def publish(client, **changes):
    start = (datetime.now(timezone.utc) + timedelta(days=10)).replace(
        hour=10, minute=0, second=0, microsecond=0, tzinfo=None
    )
    form = dict(
        starts_at=start.isoformat(timespec="minutes"),
        duration_minutes="50",
        timezone="Europe/Moscow",
        url="https://example.org/meeting",
        fallback="Связаться по согласованному телефону",
    )
    return post(client, "/doctor/calendar/slots", **(form | changes))


def query(client, sql, args=()):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def test_published_slots_book_immediately_and_survive_restart(client):
    login(client, "patient1")
    html = client.get("/patients/1/profile/video").text
    assert "нет опубликованных" in html and 'name="preferred_day"' not in html
    assert (
        post(
            client,
            "/patients/1/consultations",
            preferred_day="2030-01-01",
            time_window="10:00",
            timezone_label="UTC",
        ).status_code
        == 422
    )
    login(client, "doctor")
    assert publish(client).status_code == 200
    sid = query(client, "SELECT id FROM consultation_slots")[0]["id"]
    login(client, "patient1")
    html = client.get("/patients/1/profile/video").text
    assert "Свободное время врача" in html and 'name="slot_id"' in html
    for _ in range(2):
        assert post(client, "/patients/1/consultations", slot_id=sid).status_code == 200
    visits = query(client, "SELECT * FROM appointments")
    assert len(visits) == 1 and visits[0]["slot_id"] == sid and visits[0]["ends_at"]
    booking = query(client, "SELECT * FROM consultation_requests")[0]
    assert (
        booking["status"] == "confirmed"
        and booking["appointment_id"] == visits[0]["id"]
    )
    assert client.get("/patients/1/meetings").url.path == "/patients/1/profile/video"
    login(client, "patient2")
    assert f'value="{sid}"' not in client.get("/patients/2/profile/video").text
    assert post(client, "/patients/2/consultations", slot_id=sid).status_code == 409
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        html = restarted.get("/patients/1/profile/video").text
        assert "Время подтверждено" in html and "https://example.org/meeting" in html
        assert "Личный профиль" in html and "Разделы личного профиля" in html


def test_cancel_booking_releases_window_and_preserves_history(client):
    login(client, "doctor")
    assert publish(client).status_code == 200
    login(client, "patient1")
    assert post(client, "/patients/1/consultations", slot_id=1).status_code == 200
    assert (
        post(
            client, "/patients/1/appointments/1/status", intent="cancelled"
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert post(client, "/doctor/calendar/slots/1/cancel").status_code == 409
    assert (
        post(
            client, "/patients/1/appointments/1/status", intent="cancelled"
        ).status_code
        == 200
    )
    assert query(client, "SELECT status FROM consultation_slots")[0]["status"] == "open"
    login(client, "patient2")
    assert post(client, "/patients/2/consultations", slot_id=1).status_code == 200
    assert [
        r["status"] for r in query(client, "SELECT * FROM appointments ORDER BY id")
    ] == ["cancelled", "planned"]
    assert len(query(client, "SELECT * FROM consultation_slot_events")) == 4


def test_overlaps_expiry_csrf_and_scopes(client):
    login(client, "doctor")
    assert publish(client).status_code == 200
    assert publish(client).status_code == 409
    assert publish(client, starts_at="2000-01-01T10:00").status_code == 422
    assert publish(client, duration_minutes="0").status_code == 422
    assert publish(client, timezone="Guess").status_code == 422
    assert publish(client, url="javascript:alert(1)").status_code == 422
    assert (
        client.post(
            "/doctor/calendar/slots/1/cancel", data={"csrf": "wrong"}
        ).status_code
        == 403
    )
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Второй врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    login(client, "patient2")
    assert 'name="slot_id"' not in client.get("/patients/2/profile/video").text
    assert post(client, "/patients/2/consultations", slot_id=1).status_code == 404
    assert post(client, "/doctor/calendar/slots/1/cancel").status_code == 403
    login(client, "patient1", consent=False)
    assert post(client, "/patients/1/consultations", slot_id=1).status_code == 403
    login(client, "doctor")
    assert post(client, "/doctor/calendar/slots/1/cancel").status_code == 200
    login(client, "patient1")
    assert post(client, "/patients/1/consultations", slot_id=1).status_code == 409


def test_simultaneous_bookings_have_one_winner(client):
    login(client, "patient1")
    login(client, "patient2")
    login(client, "doctor")
    assert publish(client).status_code == 200
    users = query(
        client, "SELECT * FROM accounts WHERE role='patient' ORDER BY patient_id"
    )
    barrier = Barrier(2)

    def reserve(user):
        barrier.wait(timeout=10)
        try:
            with connect(client.app.state.db_path) as c:
                c.execute("BEGIN IMMEDIATE")
                return book(c, user, user["patient_id"], 1)
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(reserve, users))
    assert sorted(result) == [1, 409]
    assert len(query(client, "SELECT * FROM appointments")) == 1
    assert len(query(client, "SELECT * FROM consultation_requests")) == 1


def test_legacy_visit_needs_duration_without_rewriting_old_fields(client):
    login(client, "doctor")
    start = (
        (datetime.now(timezone.utc) + timedelta(days=10))
        .replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=None)
        .isoformat(timespec="minutes")
    )
    assert (
        post(
            client,
            "/patients/1/appointments",
            starts_at=start,
            timezone_label="Europe/Moscow",
            url="https://example.org/old",
            fallback="Телефон",
        ).status_code
        == 200
    )
    before = query(client, "SELECT * FROM appointments")[0]
    assert publish(client).status_code == 409
    assert (
        post(
            client, "/patients/1/appointments/1/duration", duration_minutes="50"
        ).status_code
        == 200
    )
    assert publish(client).status_code == 200
    after = query(client, "SELECT * FROM appointments")[0]
    assert all(after[k] == v for k, v in before.items() if k != "ends_at")
    with connect(client.app.state.db_path) as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_material_cancel_repeat_and_reassign_history(client):
    login(client, "doctor")
    assert (
        post(
            client,
            "/doctor/resources/1/assign",
            patient_id="1",
            note="Первое назначение",
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert post(client, "/material-assignments/1/cancel").status_code == 403
    assert (
        post(client, "/material-assignments/1/status", status="read").status_code == 200
    )
    login(client, "doctor")
    for _ in range(2):
        assert post(client, "/material-assignments/1/cancel").status_code == 200
    old = query(client, "SELECT * FROM resource_assignments")[0]
    assert old["status"] == "read" and old["cancelled_at"]
    login(client, "patient1")
    assert "Первое назначение" not in client.get("/me/materials").text
    assert (
        client.get("/material-assignments/1/open", follow_redirects=False).status_code
        == 410
    )
    assert (
        post(client, "/material-assignments/1/status", status="read").status_code == 410
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert "Назначение отменено" in restarted.get("/doctor/resources").text
        assert (
            post(
                restarted,
                "/doctor/resources/1/assign",
                patient_id="1",
                note="Новое назначение",
            ).status_code
            == 200
        )
    assignments = query(client, "SELECT * FROM resource_assignments")
    assert (
        len(assignments) == 1
        and assignments[0]["cancelled_at"] is None
        and assignments[0]["status"] == "assigned"
    )
    events = query(client, "SELECT * FROM resource_events ORDER BY id")
    assert [r["kind"] for r in events] == [
        "assigned",
        "read",
        "cancelled",
        "reassigned",
    ]
    assert json.loads(events[2]["payload"])["note"] == "Первое назначение"
    assert json.loads(events[3]["payload"])["cancelled_at"] == old["cancelled_at"]


def test_material_cancel_forbidden_to_other_doctor(client):
    login(client, "doctor")
    post(client, "/doctor/resources/1/assign", patient_id="1")
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=1")
    assert post(client, "/material-assignments/1/cancel").status_code == 403
    assert (
        query(client, "SELECT cancelled_at FROM resource_assignments")[0][
            "cancelled_at"
        ]
        is None
    )


def test_latest_diary_and_note_have_collapsible_history_and_header_exit(client):
    login(client, "patient1")
    for day, text in [
        ("2026-10-01", "Первая запись"),
        ("2026-10-02", "Последняя запись"),
    ]:
        assert (
            post(
                client,
                "/patients/1/diary",
                day=day,
                wellbeing=5,
                sleep=8,
                adherence="none",
                note=text,
            ).status_code
            == 200
        )
    for text in ["Первая заметка", "Последняя заметка"]:
        assert post(client, "/patients/1/notes", body=text).status_code == 200
    html = client.get("/patients/1/diary").text
    assert html.index('id="notes"') < html.index('id="diary-history"')
    assert "Ещё записи (1)" in html and "Ещё заметки (1)" in html
    assert html.index("Последняя запись</p>") < html.index('id="diary-older"')
    assert html.index("Последняя заметка</p>") < html.index('id="notes-older"')
    header = html.split('<header class="site-header">')[1].split("</header>")[0]
    assert header.count('href="/help-now"') == 1 and 'action="/logout"' in header
    assert "О приложении" not in client.get("/me/materials").text
    assert post(client, "/logout").status_code == 200
    assert client.get("/patients/1/diary").status_code == 401
    login(client, "doctor")
    header = (
        client.get("/doctor")
        .text.split('<header class="site-header">')[1]
        .split("</header>")[0]
    )
    assert (
        "Ещё" not in header
        and "Специалист" not in header
        and 'href="/help-now"' not in header
    )


def test_slot_duration_is_elapsed_time_across_dst(client, monkeypatch):
    from app.services import consultations

    monkeypatch.setattr(
        consultations, "utc_now", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    login(client, "doctor")
    assert (
        publish(
            client,
            starts_at="2026-03-29T01:30",
            timezone="Europe/Berlin",
            duration_minutes="60",
        ).status_code
        == 200
    )
    slot = query(client, "SELECT * FROM consultation_slots")[0]
    assert datetime.fromisoformat(slot["ends_at"]) - datetime.fromisoformat(
        slot["starts_at"]
    ) == timedelta(hours=1)
    assert (
        publish(
            client, starts_at="2026-03-29T02:30", timezone="Europe/Berlin"
        ).status_code
        == 422
    )
    assert (
        publish(
            client, starts_at="2026-10-25T02:30", timezone="Europe/Berlin"
        ).status_code
        == 422
    )
