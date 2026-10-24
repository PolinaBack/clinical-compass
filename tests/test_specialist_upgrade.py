"""Real calendar writes, combined questionnaires and submitted-only profile data."""

import re
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token
from tests.test_intake import save, send, entity
from tests.test_meetings import grants, upload, recording_id, SyntheticModels
from app.services.meetings import process_one


def calendar_form(client, **changes):
    html = client.get("/doctor/calendar").text
    nonce = re.search(r'name="calendar_token" value="([^"]+)"', html).group(1)
    day = (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
    return (
        dict(
            csrf=token(client),
            calendar_token=nonce,
            date=day,
            time="10:00",
            duration_minutes="50",
            timezone="Europe/Moscow",
            patient_id="",
            url="https://example.org/video",
            fallback="Согласованный резерв",
        )
        | changes
    )


def records(client, table):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM " + table)]


def test_navigation_only_patient_cards_and_forums_after_library(client):
    login(client, "doctor")
    page = client.get("/doctor").text
    main = page.split('<main id="main">')[1].split("</main>")[0]
    assert main.count('class="card patient-preview"') == 2
    for text in (
        "ИИ-анализ",
        "workflow-alerts",
        "Важное о пациентах",
        "Эксперимент",
        "demo-line",
    ):
        assert text not in main
    nav = re.search(r'<nav class="portal-nav".*?</nav>', page, re.S).group()
    assert "/doctor/tasks" not in nav and nav.index("Библиотека") < nav.index(
        "Форумы"
    ) < nav.index("Настройки")
    assert 'id="clinician-forum"' in client.get("/doctor/forums").text
    assert 'id="faq-forum"' in client.get("/doctor/forums").text
    assert client.get("/doctor/tasks").url.path == "/doctor/forums"
    card = client.get("/patients/1").text
    cardnav = re.search(
        r'<nav class="tab-nav specialist-nav".*?</nav>', card, re.S
    ).group()
    assert "Безопасность" not in cardnav and "Консультации" not in cardnav
    assert "Анкеты, назначения и динамика" in cardnav and "ИИ-анализ" in cardnav
    assert "Разделы личного профиля" not in card
    assert client.get("/patients/1/profile/video").url.path == "/doctor/calendar"
    assert (
        client.get("/patients/1/results").url.path
        == "/patients/1/workspace/assessments"
    )


def test_visual_calendar_publishes_books_prevents_duplicates_and_overlaps(client):
    login(client, "doctor")
    data = calendar_form(client)
    for _ in range(2):
        assert client.post("/doctor/calendar/events", data=data).status_code == 200
    assert len(records(client, "consultation_slots")) == 1
    assert not records(client, "appointments")
    html = client.get("/doctor/calendar?week=" + data["date"]).text
    assert "data-calendar-time=" in html and 'data-calendar-slot="1"' in html
    assert "top:320.0px;height:28px" in html
    assert (
        client.post(
            "/doctor/calendar/events", data=calendar_form(client, time="10:30")
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/doctor/calendar/events",
            data=calendar_form(client, time="11:00", patient_id="1"),
        ).status_code
        == 200
    )
    visits = records(client, "appointments")
    assert (
        len(visits) == 1 and visits[0]["patient_id"] == 1 and visits[0]["slot_id"] == 2
    )
    assert (
        client.post(
            "/patients/1/appointments/1/status",
            data=dict(csrf=token(client), intent="cancelled"),
        ).status_code
        == 200
    )
    assert records(client, "consultation_slots")[1]["status"] == "open"
    login(client, "patient1")
    assert "Свободное время врача" in client.get("/patients/1/profile/video").text
    assert client.get("/doctor/calendar").status_code == 403
    assert client.get("/doctor/forums").status_code == 403
    assert client.post("/doctor/calendar/events", data=data).status_code == 403


def test_calendar_source_times_zone_cross_midnight_and_scopes(client):
    login(client, "doctor")
    data = calendar_form(client, time="23:30", duration_minutes="60", patient_id="1")
    assert client.post("/doctor/calendar/events", data=data).status_code == 200
    html = client.get("/doctor/calendar?view=day&week=" + data["date"]).text
    assert "top:752.0px;height:28px" in html
    nextday = (
        (datetime.fromisoformat(data["date"]) + timedelta(days=1)).date().isoformat()
    )
    assert (
        "top:0.0px;height:28px"
        in client.get("/doctor/calendar?view=day&week=" + nextday).text
    )
    # Moscow 23:30 is 20:30 UTC; positions must follow the chosen displayed zone.
    assert (
        "top:656.0px;height:32.0px"
        in client.get(
            "/doctor/calendar?view=day&timezone=UTC&week=" + data["date"]
        ).text
    )
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    assert client.get("/doctor/calendar?patient_id=2").status_code == 403
    assert (
        client.post(
            "/doctor/calendar/events", data=calendar_form(client, patient_id="2")
        ).status_code
        == 403
    )
    assert (
        client.get("/doctor/calendar?patient_id=1&appointment_id=999").status_code
        == 404
    )
    assert (
        client.post("/doctor/calendar/events", data=data | dict(csrf="bad")).status_code
        == 403
    )
    assert (
        client.post(
            "/doctor/calendar/events", data=calendar_form(client, timezone="Not/AZone")
        ).status_code
        == 422
    )
    assert client.get("/doctor/calendar?week=bad").status_code == 422
    assert client.get("/doctor/calendar?patient_id=" + str(2**80)).status_code == 422
    assert (
        client.post(
            "/doctor/calendar/events", data=calendar_form(client, patient_id=str(2**80))
        ).status_code
        == 422
    )


def test_calendar_embeds_audio_processing_and_patient_specific_summary(
    client, monkeypatch
):
    login(client, "patient1")
    login(client, "doctor")
    data = calendar_form(client, date="2020-01-01", patient_id="1", url="", fallback="")
    response = client.post("/doctor/calendar/events", data=data)
    assert response.status_code == 200 and response.url.path == "/doctor/calendar"
    aid = records(client, "appointments")[0]["id"]
    response = client.post(
        f"/patients/1/appointments/{aid}/meeting",
        data=dict(csrf=token(client), calendar="true"),
    )
    assert response.status_code == 200 and response.url.path == "/doctor/calendar"
    meeting = records(client, "meeting_sessions")[0]
    mid = meeting["id"]
    grants(client, meeting["context_id"])
    assert (
        upload(client, mid, calendar="true")
        .headers["location"]
        .startswith("/doctor/calendar?")
    )
    rid = recording_id(client)
    for kind in ("transcript", "summary"):
        r = client.post(
            f"/meeting-recordings/{rid}/jobs/{kind}",
            data=dict(csrf=token(client), calendar="true"),
        )
        assert r.status_code == 200 and r.url.path == "/doctor/calendar"
        assert process_one(client.app.state.db_path, SyntheticModels())
    html = client.get(f"/doctor/calendar?patient_id=1&meeting_id={mid}").text
    assert 'name="calendar" value="true"' in html
    assert "Черновик итогов" in html and "Следующие шаги" in html
    assert "Расшифровать запись" in html
    assert re.search(
        r'<a href="/doctor/calendar[^"\n]*#recording-'
        + str(rid)
        + r'">Показать итоги</a>',
        html,
    )
    assert "synthetic.wav" not in client.get("/doctor/calendar?patient_id=2").text
    assert (
        client.get(f"/doctor/calendar?patient_id=2&meeting_id={mid}").status_code == 404
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert (
            "synthetic.wav"
            in restarted.get(f"/doctor/calendar?patient_id=1&meeting_id={mid}").text
        )
    login(client, "patient1")
    assert client.get(f"/meetings/{mid}").status_code == 200
    assert "synthetic.wav" not in client.get(f"/meetings/{mid}").text
    assert client.get(f"/meetings/{mid}/jobs").status_code == 403


def test_clinician_summary_uses_submitted_data_and_preserves_private_draft(client):
    login(client, "patient1")
    assert (
        save(
            client,
            "personal",
            a_preferred_name="Обращение из отправленной анкеты",
            a_birth_date="1994-01-01",
            a_sex="Женский",
        ).status_code
        == 200
    )
    assert (
        save(client, "measurements", a_height="175", a_weight="75").status_code == 200
    )
    assert (
        save(
            client, "complaints", a_groups=["Тревога и страх", "Сон и энергия"]
        ).status_code
        == 200
    )
    assert entity(client, "condition", a_name="Сведения о мигрени").status_code == 200
    assert (
        entity(
            client,
            "medication",
            a_name="Текущее средство",
            a_category="Назначено, принимаю сейчас",
        ).status_code
        == 200
    )
    assert (
        entity(
            client,
            "medication",
            a_name="Прежнее средство",
            a_category="Принимал(а) раньше",
        ).status_code
        == 200
    )
    assert (
        entity(
            client, "reaction", a_name="Неуточнённая реакция", a_type="Не знаю"
        ).status_code
        == 200
    )
    assert send(client).status_code == 200
    assert (
        save(
            client, "personal", a_preferred_name="Секретный неотправленный черновик"
        ).status_code
        == 200
    )
    login(client, "doctor")
    html = client.get("/patients/1/profile").text
    assert (
        "Краткое медицинское резюме" in html and "Вопросы, требующие проверки" in html
    )
    assert "175 см" in html and "75 кг" in html and "ИМТ 24.5" in html
    assert "Сведения о мигрени" in html and "Текущее средство" in html
    summary = html.split('<dl class="medical-summary">')[1].split("</dl>")[0]
    assert "Прежнее средство" not in summary
    assert "Обращение из отправленной анкеты" in html
    assert "Секретный неотправленный черновик" not in html
    assert "Прежнее средство" in client.get("/patients/1/profile?view=medicines").text
    assert "Разделы личного профиля" not in html
