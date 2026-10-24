"""E02: navigation, real data, durable presentation choices and report access."""

from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, now
from tests.helpers import login, token


def test_public_emergency_requires_no_login_or_consent(client):
    page = client.get("/help-now")
    assert page.status_code == 200
    assert "<h1>Экстренная помощь</h1>" in page.text
    assert 'href="tel:112"' in page.text
    assert "При непосредственной опасности" in page.text
    assert "местный номер экстренной помощи" in page.text
    assert "Запись на консультацию и отметка" not in page.text
    assert "Не обещаем поиск человека" not in page.text
    assert "Мне нужна помощь сейчас" not in client.get("/faq").text
    assert "Экстренная помощь" in client.get("/faq").text


def test_patient_navigation_and_new_entry_points_are_functional(client):
    login(client, "patient2", consent=False)
    for path in [
        "/me/overview",
        "/me/materials",
        "/patients/2/profile",
        "/patients/2/diary",
        "/patients/2/results",
        "/settings",
    ]:
        page = client.get(path)
        assert page.status_code == 200, path
        assert 'aria-label="Навигация пациента"' in page.text
        for href in [
            "/patients/2/diary",
            "/patients/2/results",
            "/me/materials",
            "/patients/2/profile",
        ]:
            assert f'href="{href}"' in page.text
    assert client.get("/doctor/calendar").status_code == 403
    assert client.get("/doctor/tasks").status_code == 403
    assert client.get("/doctor/library").status_code == 403
    assert client.get("/patients/1/reports").status_code == 403


def test_interface_preferences_only_control_decoration_and_keep_legacy_data(client):
    login(client, "patient1")
    form = dict(csrf=token(client, "/settings"), revision="0", minimal="true")
    assert client.post("/settings/interface", data=form).status_code == 200
    assert client.post("/settings/interface", data=form).status_code == 409
    assert (
        client.post(
            "/settings/interface",
            data={**form, "revision": "1", "wellbeing_visible": ""},
        ).status_code
        == 403
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        html = restarted.get("/patients/1/diary").text
        assert "minimal-ui" in html and "wellbeing-dialog" in html
        assert "Карточки на главной" not in restarted.get("/settings").text
    login(client, "patient2")
    assert " minimal-ui" not in client.get("/settings").text


def test_doctor_calendar_and_tasks_use_existing_assigned_records(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
        for pid in (1, 2):
            c.execute(
                "INSERT INTO consultation_requests(patient_id,preferred_day,time_window,timezone_label,created_at) VALUES(?,'2026-11-01',?,'Europe/Moscow',?)",
                (pid, f"Заявка-{pid}", now()),
            )
            c.execute(
                "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(?,1,'2026-11-01T10:00','Europe/Moscow','https://example.org','Телефон',?)",
                (pid, now()),
            )
    for path in ("/doctor/calendar",):
        html = client.get(path).text
        assert "Заявка-1" in html and "Заявка-2" not in html
        assert 'href="/doctor/calendar?patient_id=1#meeting-workspace"' in html
        assert 'href="/patients/2/profile/video' not in html
    assert client.get("/doctor/tasks").url.path == "/doctor/forums"
    assert 'id="workflow-alerts"' in client.get("/doctor/calendar").text
    assert client.get("/doctor/library").status_code == 200
    assert client.get("/me/overview").status_code == 403
    assert (
        client.post(
            "/settings/interface",
            data=dict(csrf=token(client, "/settings"), revision="0", minimal="true"),
        ).status_code
        == 200
    )
    assert "minimal-ui" in client.get("/doctor").text


def test_retired_reports_preserve_archived_rows_without_exposing_them(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO specialist_reports(patient_id,author_id,title,self_report,observations,conclusion,plan,published,created_at) VALUES(1,1,'Старый отчёт','Рассказ','Наблюдение','Архивное заключение','План',1,?)",
            (now(),),
        )
        original = dict(c.execute("SELECT * FROM specialist_reports").fetchone())
    assert client.get("/patients/1/reports").status_code == 410
    assert (
        client.post(
            "/patients/1/reports/1/publish", data=dict(csrf=token(client))
        ).status_code
        == 410
    )
    assert "/reports" not in client.get("/patients/1/results").text
    login(client, "patient1")
    assert "Архивное заключение" not in client.get("/patients/1/results").text
    assert client.get("/patients/1/reports").status_code == 410
    login(client, "patient2")
    assert client.get("/patients/1/reports").status_code == 403
    with connect(client.app.state.db_path) as c:
        assert (
            dict(c.execute("SELECT * FROM specialist_reports").fetchone()) == original
        )
