"""E02: navigation, real data, durable presentation choices and report access."""

from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, now
from app.services.interface import CARDS, read_interface
from tests.helpers import login, token


def interface_form(client, **changes):
    data = dict(csrf=token(client, "/settings"), revision="0")
    for i, (key, _) in enumerate(CARDS, 1):
        data.update({key + "_visible": "true", key + "_position": str(i)})
    data.update(changes)
    return data


def test_public_emergency_requires_no_login_or_consent(client):
    page = client.get("/help-now")
    assert page.status_code == 200
    assert '<h1>Экстренная помощь</h1>' in page.text
    assert 'href="tel:112"' in page.text
    assert "При непосредственной опасности" in page.text
    assert "местный номер экстренной помощи" in page.text
    assert "Запись на консультацию и отметка" not in page.text
    assert "Не обещаем поиск человека" not in page.text
    assert "Мне нужна помощь сейчас" not in client.get("/faq").text
    assert "Экстренная помощь" in client.get("/faq").text


def test_patient_navigation_and_new_entry_points_are_functional(client):
    login(client, "patient2", consent=False)
    for path in ["/me/overview", "/me/materials", "/patients/2/profile", "/patients/2/diary",
                 "/patients/2/results", "/patients/2/reports", "/settings"]:
        page = client.get(path)
        assert page.status_code == 200, path
        assert 'aria-label="Навигация пациента"' in page.text
        for href in ["/me/overview", "/patients/2/diary", "/patients/2/results", "/me/materials", "/patients/2/profile"]:
            assert f'href="{href}"' in page.text
    assert client.get("/doctor/calendar").status_code == 403
    assert client.get("/doctor/tasks").status_code == 403
    assert client.get("/doctor/library").status_code == 403
    assert client.get("/patients/1/reports").status_code == 403


def test_overview_reads_latest_diary_by_day_not_insert_id(client):
    login(client, "patient1")
    with connect(client.app.state.db_path) as c:
        for day, value in [("2026-01-20", 8), ("2026-01-10", 2)]:
            c.execute("INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(1,?,?,7.5,'taken','',0)", (day, value))
    html = client.get("/me/overview").text
    assert "8 из 10" in html and "2 из 10" not in html
    assert "2026-01-20" in html and "7.5 ч" in html
    login(client, "patient2")
    assert "Нет данных" in client.get("/me/overview").text
    assert "8 из 10" not in client.get("/me/overview").text


def test_interface_preferences_persist_and_cannot_change_clinical_records(client):
    login(client, "patient1")
    form = interface_form(client, minimal="true", wellbeing_visible="", consultations_pinned="true",
                          sleep_position="1", scales_position="2")
    assert client.post("/settings/interface", data=form).status_code == 200
    html = client.get("/me/overview").text
    assert "minimal-ui" in html and 'data-card="wellbeing"' not in html
    assert html.index('data-card="consultations"') < html.index('data-card="sleep"') < html.index('data-card="scales"')
    assert 'href="/patients/1/diary"' in html and 'href="/help-now"' in html
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM consent_events").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM assessment_events").fetchone()[0] == 0
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1", consent=False)
        assert 'data-card="wellbeing"' not in restarted.get("/me/overview").text
        assert "minimal-ui" in restarted.get("/settings").text
    login(client, "patient2")
    html = client.get("/me/overview").text
    assert 'data-card="wellbeing"' in html and " minimal-ui" not in html


def test_interface_rejects_csrf_invalid_input_and_stale_tabs_atomically(client):
    login(client, "patient1")
    form = interface_form(client)
    assert client.post("/settings/interface", data={**form, "csrf": "wrong"}).status_code == 403
    assert client.post("/settings/interface", data={**form, "sleep_position": "0"}).status_code == 422
    assert client.post("/settings/interface", data={**form, "minimal": "yes"}).status_code == 422
    assert client.post("/settings/interface", data={**form, "minimal": "true"}).status_code == 200
    assert client.post("/settings/interface", data=form).status_code == 409
    with connect(client.app.state.db_path) as c:
        uid = c.execute("SELECT id FROM accounts WHERE username='patient1'").fetchone()[0]
        state = read_interface(c, uid)
        assert state["minimal"] and state["revision"] == 1


def test_doctor_calendar_and_tasks_use_existing_assigned_records(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
        for pid in (1, 2):
            c.execute("INSERT INTO consultation_requests(patient_id,preferred_day,time_window,timezone_label,created_at) VALUES(?,'2026-11-01',?,'Europe/Moscow',?)", (pid, f'Заявка-{pid}', now()))
            c.execute("INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(?,1,'2026-11-01T10:00','Europe/Moscow','https://example.org','Телефон',?)", (pid, now()))
    for path in ("/doctor/calendar", "/doctor/tasks"):
        html = client.get(path).text
        assert "Заявка-1" in html and "Заявка-2" not in html
        assert 'href="/patients/1/profile/video#booking"' in html
        assert 'href="/patients/2/profile/video' not in html
    assert 'id="workflow-alerts"' in client.get("/doctor/tasks").text
    assert client.get("/doctor/library").status_code == 200
    assert client.get("/me/overview").status_code == 403
    assert client.post("/settings/interface", data=dict(csrf=token(client, "/settings"), revision="0", minimal="true")).status_code == 200
    assert "minimal-ui" in client.get("/doctor").text


def test_reports_move_preserves_drafts_publishing_and_authorization(client):
    login(client, "doctor")
    data = dict(csrf=token(client), title="Отчёт E02", self_report="Рассказ", observations="Наблюдения",
                conclusion="Частное заключение", plan="План")
    response = client.post("/patients/1/reports", data=data)
    assert response.url.path == "/patients/1/reports"
    assert "Частное заключение" in response.text
    assert 'id="reports"' not in client.get("/patients/1/results").text
    with connect(client.app.state.db_path) as c:
        rid = c.execute("SELECT id FROM specialist_reports").fetchone()[0]
    login(client, "patient1")
    assert "Частное заключение" not in client.get("/patients/1/reports").text
    login(client, "doctor")
    assert client.post(f"/patients/1/reports/{rid}/publish", data=dict(csrf=token(client))).status_code == 200
    login(client, "patient1")
    assert "Частное заключение" in client.get("/patients/1/reports").text
    assert "Частное заключение" not in client.get("/patients/1/results").text
    login(client, "patient2")
    assert client.get("/patients/1/reports").status_code == 403
