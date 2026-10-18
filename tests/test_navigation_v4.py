"""Synthetic flows for the separated patient sections and public FAQ."""

import re
from datetime import date, timedelta
from app.charts import chart
from tests.helpers import login, token
from tests.test_workflows import rows


def post(client, endpoint, **data):
    return client.post(endpoint, data={"csrf": token(client), **data})


def test_initial_page_and_profile_sections_load_separately(client):
    login(client, "patient1")
    r = client.get("/me")
    assert r.url.path == "/patients/1/profile/assessments"
    assert 'id="assessments"' in r.text
    for entry in ["/", "/patients/1"]:
        assert client.get(entry).url.path == r.url.path
    nav = re.search(r'<nav class="header-nav".*?</nav>', r.text, re.S).group()
    labels = ["Личный профиль", "Дневник", "Динамика опросников", "С чего начать"]
    assert [nav.index(label) for label in labels] == sorted(
        nav.index(label) for label in labels
    )
    assert 'aria-current="page">Личный профиль' in nav
    assert "Мой специалист" not in nav
    start = client.get("/me/start")
    assert "Не обязательно" in start.text
    assert 'aria-current="page">С чего начать' in start.text
    login(client, "patient2")
    assert client.get("/me").url.path == "/patients/2/profile/assessments"
    assert client.get("/patients/1").status_code == 403
    login(client, "patient1")
    index = client.get("/patients/1/profile").text
    assert 'name="biography"' not in index and 'id="family"' not in index
    assert "reports" not in index and "Полезно каждому пациенту" not in index
    for section, present, absent in [
        ("about", 'name="biography"', 'id="family"'),
        ("family", 'id="family"', 'name="biography"'),
        ("assessments", 'id="assessments"', 'id="video"'),
        ("video", 'id="video"', 'id="family"'),
    ]:
        page = client.get("/patients/1/profile/" + section)
        assert (
            page.status_code == 200 and present in page.text and absent not in page.text
        )
    assert client.get("/patients/1/profile/invalid").status_code == 404


def test_standalone_note_is_visible_to_assigned_specialist_only(client):
    login(client, "patient1")
    assert (
        post(
            client, "/patients/1/notes", body="Учебный вопрос к следующей встрече"
        ).status_code
        == 200
    )
    page = client.get("/patients/1/diary").text
    assert "Учебный вопрос к следующей встрече" in page
    assert 'name="contact"' not in page and "Полезно каждому пациенту" not in page
    assert not rows(client, "SELECT * FROM diary")
    login(client, "doctor")
    assert "Учебный вопрос к следующей встрече" in client.get("/patients/1").text
    assert post(client, "/patients/1/notes", body="Подмена").status_code == 403
    login(client, "patient2")
    assert client.get("/patients/1/diary").status_code == 403


def test_public_question_has_no_author_link_and_requires_moderated_answer(client):
    page = client.get("/faq")
    form_id = re.search(r'name="form_id"[^>]*value="([^"]+)"', page.text).group(1)
    payload = dict(
        csrf=token(client, "/faq"),
        form_id=form_id,
        body="Синтетический общий вопрос",
        public_consent="true",
    )
    r = client.post("/faq/questions", data=payload)
    assert r.status_code == 200 and "ожидает ответа" in r.text
    assert client.post("/faq/questions", data=payload).status_code == 200
    row = rows(client, "SELECT * FROM forum_questions")[0]
    assert len(rows(client, "SELECT * FROM forum_questions")) == 1
    assert set(row) == {
        "id",
        "public_id",
        "submission_id",
        "body",
        "reply",
        "status",
        "created_at",
        "replied_at",
        "moderator_id",
    }
    assert row["moderator_id"] is None
    assert row["body"] not in client.get("/faq").text
    assert client.get("/doctor/faq").status_code == 401
    login(client, "patient1")
    assert (
        post(
            client, f"/doctor/faq/{row['id']}", intent="publish", reply="Подмена"
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert row["body"] in client.get("/doctor/faq").text
    assert (
        post(client, f"/doctor/faq/{row['id']}", intent="publish", reply="").status_code
        == 422
    )
    assert (
        post(
            client,
            f"/doctor/faq/{row['id']}",
            intent="publish",
            reply="Учебный проверенный ответ",
        ).status_code
        == 200
    )
    client.post("/logout", data={"csrf": token(client)})
    assert "Учебный проверенный ответ" in client.get("/faq").text
    assert (
        "Учебный проверенный ответ"
        in client.get("/faq/questions/" + row["public_id"]).text
    )
    login(client, "doctor")
    assert post(client, f"/doctor/faq/{row['id']}", intent="hide").status_code == 200
    assert row["body"] not in client.get("/faq").text
    assert client.get("/faq/questions/" + row["public_id"]).status_code == 404


def test_forum_csrf_publication_consent_and_escaping(client):
    base = dict(form_id="synthetic-submission-12345", body="<script>synthetic</script>")
    assert (
        client.post(
            "/faq/questions", data=base | {"csrf": "bad", "public_consent": "true"}
        ).status_code
        == 403
    )
    assert post(client, "/faq/questions", **base).status_code == 422
    r = post(client, "/faq/questions", **base, public_consent="true")
    assert r.status_code == 200 and "<script>synthetic</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_consultation_request_confirmed_by_own_doctor_only(client):
    login(client, "patient1")
    assert (
        post(
            client,
            "/patients/1/consultations",
            preferred_day=date.today().isoformat(),
            time_window="14:00–17:00",
            timezone_label="Москва UTC+3",
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            "/patients/1/consultations",
            preferred_day=date.today().isoformat(),
            time_window="14:00–17:00",
            timezone_label="Москва UTC+3",
        ).status_code
        == 200
    )
    assert len(rows(client, "SELECT * FROM consultation_requests")) == 1
    booking = rows(client, "SELECT * FROM consultation_requests")[0]
    assert booking["status"] == "requested" and booking["appointment_id"] is None
    login(client, "doctor")
    assert "Заявки на консультацию" in client.get("/doctor").text
    data = dict(
        starts_at="2026-12-01T15:00",
        timezone_label="Москва UTC+3",
        url="https://example.org/synthetic-video",
        fallback="Учебный резерв",
        booking_id=booking["id"],
    )
    assert post(client, "/patients/2/appointments", **data).status_code == 422
    assert post(client, "/patients/1/appointments", **data).status_code == 200
    updated = rows(client, "SELECT * FROM consultation_requests")[0]
    assert updated["status"] == "confirmed" and updated["appointment_id"]
    assert post(client, "/patients/1/appointments", **data).status_code == 422
    login(client, "patient1")
    page = client.get("/patients/1/profile/video").text
    assert data["url"] in page and "Время подтверждено" in page
    assert (
        post(
            client,
            "/patients/1/consultations",
            preferred_day=(date.today() - timedelta(days=1)).isoformat(),
            time_window="10:00",
            timezone_label="UTC+3",
        ).status_code
        == 422
    )


def test_family_tabs_preserve_sources_unknowns_and_ownership(client):
    login(client, "patient1")
    base = "/patients/1/profile/family/member/mother"
    assert client.get(base).status_code == 200
    assert (
        post(
            client,
            "/patients/1/family/mother",
            tab="about",
            name="Учебная мама",
            about="Вымышленный контекст",
        ).status_code
        == 200
    )
    rid = rows(client, "SELECT id FROM relatives WHERE slot='mother'")[0]["id"]
    assert (
        post(
            client,
            "/patients/1/family/mother",
            tab="knowledge",
            answer_kind="unknown",
            description="Пока не знаю",
        ).status_code
        == 200
    )
    assert 'name="about"' not in client.get(base + "?tab=knowledge").text
    assert (
        post(
            client,
            "/patients/1/family/mother",
            tab="explanations",
            explanation="Учебное уточнение",
            discuss="true",
        ).status_code
        == 200
    )
    detail = rows(client, "SELECT * FROM family_details")[0]
    assert (
        detail["answer_kind"] == "unknown"
        and detail["about"] == "Вымышленный контекст"
        and detail["discuss"] == 1
    )
    assert not rows(client, "SELECT * FROM family_conditions")
    data = dict(
        label="Сообщённое название",
        description="Рассказ близкого",
        age_kind="Возраст, когда сообщили диагноз",
        age_text="Около 48 лет",
        source="Со слов родственника",
        uncertain="true",
    )
    assert (
        post(client, f"/patients/1/family/{rid}/conditions", **data).status_code == 200
    )
    cid = rows(client, "SELECT id FROM family_conditions")[0]["id"]
    assert "Сообщённое название" in client.get(base + "?tab=knowledge").text
    assert "family-leaf" in client.get("/patients/1/profile/family").text
    assert "family-list-item" in client.get("/patients/1/profile/family?view=list").text
    login(client, "doctor")
    assert "Рассказ близкого" in client.get(base + "?tab=knowledge").text
    assert (
        post(
            client, "/patients/1/family/mother", tab="about", name="Подмена"
        ).status_code
        == 403
    )
    login(client, "patient2")
    assert client.get(base).status_code == 403
    assert (
        post(client, f"/patients/2/family/conditions/{cid}/delete").status_code == 404
    )
    login(client, "patient1")
    assert (
        post(client, f"/patients/1/family/conditions/{cid}/delete").status_code == 200
    )
    assert not rows(client, "SELECT * FROM family_conditions")


def test_graph_calendar_axes_and_null_gaps(client):
    graph = chart(
        [
            dict(id=1, period_end="2026-10-01", total=2),
            dict(id=2, period_end="2026-10-02", total=None),
            dict(id=3, period_end="2026-10-11", total=4),
        ],
        27,
        "PHQ-9",
        "phq9",
    )
    assert len(graph["segments"]) == 2
    assert graph["points"][0]["x"] == 85 and graph["points"][1]["x"] == 595
    assert graph["x_label"] == "Дата оценки" and graph["y_label"] == "Баллы PHQ-9"
    login(client, "patient1")
    post(
        client,
        "/patients/1/diary",
        day=date.today().isoformat(),
        wellbeing=5,
        sleep=8,
        adherence="none",
    )
    html = client.get("/patients/1/diary").text
    assert "Дата записи" in html and "Самочувствие, 0–10" in html
    dynamics = client.get("/patients/1/results").text
    assert "diary-chart" not in dynamics and "Самочувствие по дневнику" not in dynamics
    assert "01.10.2026" == graph["xticks"][0]["label"]
