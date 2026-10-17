import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from app.main import create_app


def token(client, path="/"):
    page = client.get(path)
    assert page.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)


def login(client, role):
    response = client.post("/login", data={"role": role, "csrf": token(client)})
    assert response.status_code == 200


def post(client, endpoint, **data):
    data.setdefault("csrf", token(client, "/patients/1"))
    return client.post(endpoint, data=data)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as c:
        yield c


def test_end_to_end_patient_doctor_patient(client):
    login(client, "patient1")
    r = post(
        client,
        "/patients/1/diary",
        day=date.today().isoformat(),
        wellbeing=6,
        sleep=7.5,
        adherence="missed",
        note="Синтетическая запись",
        contact="true",
    )
    assert r.status_code == 200
    assert "Синтетическая запись" in r.text
    assert "пропуск назначения" in r.text
    assert "polyline points=" in r.text
    assert (
        post(
            client, "/patients/1/screening", tension=4, difficulty=2, note="Демоанкета"
        ).status_code
        == 200
    )
    assert (
        post(client, "/patients/1/messages", body="Демовопрос врачу").status_code == 200
    )
    assert (
        post(
            client,
            "/patients/1/risks",
            kind="event",
            body="Демо-событие",
            urgent="true",
        ).status_code
        == 200
    )
    login(client, "doctor")
    assert "1 записей" in client.get("/doctor").text
    card = client.get("/patients/1").text
    assert all(
        text in card for text in ["Демовопрос врачу", "Демоанкета", "Демо-событие"]
    )
    alerts = client.get("/api/alerts").json()["alerts"]
    assert (
        len(alerts) == 1 and alerts[0]["urgent"] == 1 and alerts[0]["acknowledged"] == 0
    )
    assert post(client, "/patients/1/acknowledge", last_id=1).status_code == 200
    assert client.get("/api/alerts").json()["alerts"][0]["acknowledged"] == 1
    assert (
        post(client, "/patients/1/messages", body="Демоответ врача").status_code == 200
    )
    assert (
        post(
            client,
            "/patients/1/decisions",
            category="recommended",
            body="Демо-решение, не клиническая рекомендация",
            source="Синтетический сценарий",
            confirmed="true",
        ).status_code
        == 200
    )
    login(client, "patient1")
    card = client.get("/patients/1").text
    assert "Демоответ врача" in card and "ЖЕЛАТЕЛЬНО" in card


@pytest.mark.parametrize("path", ["/patients/1", "/doctor", "/api/alerts"])
def test_anonymous_denied(client, path):
    assert client.get(path).status_code == 401


def test_role_and_patient_isolation(client):
    login(client, "patient2")
    assert client.get("/patients/1").status_code == 403
    assert client.get("/doctor").status_code == 403
    assert client.get("/api/alerts").status_code == 403
    csrf = token(client, "/patients/2")
    assert (
        client.post(
            "/patients/1/messages", data={"csrf": csrf, "body": "forbidden"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/patients/2/decisions",
            data={
                "csrf": csrf,
                "category": "required",
                "body": "x",
                "source": "demo",
                "confirmed": "true",
            },
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert (
        post(
            client,
            "/patients/1/diary",
            day=date.today().isoformat(),
            wellbeing=5,
            sleep=8,
            adherence="none",
        ).status_code
        == 403
    )
    assert client.get("/patients/999").status_code == 404


def test_csrf_and_session_rotation(client):
    old = token(client)
    assert (
        client.post("/login", data={"role": "doctor", "csrf": "bad"}).status_code == 403
    )
    login(client, "patient1")
    assert post(client, "/patients/1/messages", csrf=old, body="bad").status_code == 403
    assert (
        post(client, "/patients/1/messages", csrf="bad", body="bad").status_code == 403
    )
    assert post(client, "/patients/1/messages", csrf="", body="bad").status_code == 403


@pytest.mark.parametrize(
    "override",
    [
        {"wellbeing": 11},
        {"wellbeing": -1},
        {"sleep": 25},
        {"sleep": "nan"},
        {"adherence": "invented"},
        {"day": (date.today() + timedelta(days=1)).isoformat()},
        {"day": "not-a-date"},
        {"note": "x" * 3001},
    ],
)
def test_diary_validation(client, override):
    login(client, "patient1")
    values = dict(day=date.today().isoformat(), wellbeing=5, sleep=8, adherence="none")
    values.update(override)
    assert post(client, "/patients/1/diary", **values).status_code == 422


def test_screening_and_decision_validation(client):
    login(client, "patient1")
    assert (
        post(client, "/patients/1/screening", tension=11, difficulty=3).status_code
        == 422
    )
    assert post(client, "/patients/1/messages", body="  ").status_code == 422
    assert (
        post(client, "/patients/1/risks", kind="invalid", body="demo").status_code
        == 422
    )
    login(client, "doctor")
    assert (
        post(
            client,
            "/patients/1/decisions",
            category="required",
            body="demo",
            source="demo",
        ).status_code
        == 422
    )
    assert "ОБЯЗАТЕЛЬНО" not in client.get("/patients/1").text


def test_genogram_and_foreign_parent(client):
    login(client, "patient1")
    assert (
        post(
            client, "/patients/1/relatives", name="Демо-мать", relation="mother"
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            "/patients/1/relatives",
            name="Демо-пациент",
            relation="self",
            parent_id="1",
        ).status_code
        == 200
    )
    assert "Демо-мать" in client.get("/patients/1").text
    assert (
        post(
            client,
            "/patients/1/relatives",
            name="Demo",
            relation="other",
            parent_id="999",
        ).status_code
        == 422
    )
    login(client, "patient2")
    csrf = token(client, "/patients/2")
    assert (
        client.post(
            "/patients/2/relatives",
            data={"csrf": csrf, "name": "Demo", "relation": "other", "parent_id": "1"},
        ).status_code
        == 422
    )


def test_xss_escaped(client):
    login(client, "patient1")
    payload = '<script>alert("demo")</script>'
    response = post(client, "/patients/1/messages", body=payload)
    assert payload not in response.text
    assert "&lt;script&gt;" in response.text
    response = post(client, "/patients/1/relatives", name=payload, relation="self")
    assert payload not in response.text
    assert "\\u003cscript\\u003e" in response.text


def test_new_alert_after_acknowledgement(client):
    login(client, "patient1")
    post(client, "/patients/1/risks", kind="risk", body="First")
    login(client, "doctor")
    post(client, "/patients/1/acknowledge", last_id=1)
    login(client, "patient1")
    post(client, "/patients/1/risks", kind="event", body="Second", urgent="true")
    login(client, "doctor")
    rows = client.get("/api/alerts").json()["alerts"]
    assert rows[0]["body"] == "Second" and rows[0]["acknowledged"] == 0
    assert rows[1]["acknowledged"] == 1


def test_persistence_after_restart(tmp_path):
    path = tmp_path / "persistent.db"
    with TestClient(create_app(path)) as first:
        login(first, "patient1")
        post(first, "/patients/1/messages", body="Survives restart")
    with TestClient(create_app(path)) as second:
        login(second, "patient1")
        assert "Survives restart" in second.get("/patients/1").text


def test_public_sources_and_static_files(client):
    assert client.get("/sources").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_acknowledgement_does_not_hide_unseen_events(client):
    login(client, "patient1")
    post(client, "/patients/1/risks", kind="risk", body="Seen")
    post(client, "/patients/1/risks", kind="risk", body="Unseen")
    login(client, "doctor")
    assert post(client, "/patients/1/acknowledge", last_id=1).status_code == 200
    rows = client.get("/api/alerts").json()["alerts"]
    assert rows[0]["body"] == "Unseen" and rows[0]["acknowledged"] == 0
    assert rows[1]["acknowledged"] == 1
    assert post(client, "/patients/1/acknowledge", last_id=999).status_code == 422
