"""Synthetic integration cases for the post-login care workflow."""

from datetime import date

import pytest

from tests.helpers import login, token
from tests.test_workflows import rows, start, save, save_data, assign


def post(client, endpoint, **data):
    return client.post(endpoint, data={"csrf": token(client), **data})


def test_profile_separates_patient_history_from_doctor_records(client):
    login(client, "patient1")
    assert (
        post(
            client,
            "/patients/1/profile",
            biography="Синтетическая биография",
            health="Синтетический контекст",
            family_context="Не знаю; со слов родственника",
        ).status_code
        == 200
    )
    assert "Синтетическая биография" not in client.get("/patients/1/profile/about").text
    assert (
        post(client, "/patients/2/profile", biography="Чужие данные").status_code == 403
    )
    login(client, "doctor")
    assert "Синтетическая биография" not in client.get("/patients/1/profile/about").text
    assert (
        post(client, "/patients/1/profile", biography="Подмена врачом").status_code
        == 403
    )
    assert (
        rows(client, "SELECT biography FROM patient_profiles")[0]["biography"]
        == "Синтетическая биография"
    )
    login(client, "patient1")
    assert (
        post(
            client, "/patients/1/profile", biography="Обновлённая биография"
        ).status_code
        == 200
    )
    assert (
        rows(client, "SELECT family_context FROM patient_profiles")[0]["family_context"]
        == "Не знаю; со слов родственника"
    )
    login(client, "doctor")
    # Choosing either demo persona must never broaden the assigned doctor's scope.
    from app.storage import connect

    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой учебный специалист')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=1")
    for url in [
        "/patients/1/profile",
        "/patients/1/contact",
        "/patients/1/safety",
        "/clinical-tools?patient_id=1",
    ]:
        assert client.get(url).status_code == 403


def test_report_creation_and_publication_are_retired_for_both_roles(client):
    login(client, "doctor")
    assert post(client, "/patients/1/reports", title="Не создавать").status_code == 410
    assert post(client, "/patients/1/reports/1/publish").status_code == 410
    assert not rows(client, "SELECT * FROM specialist_reports")
    login(client, "patient1")
    assert post(client, "/patients/1/reports").status_code == 403
    assert post(client, "/patients/1/reports/1/publish").status_code == 403
    assert client.get("/patients/1/reports").status_code == 410
    login(client, "patient2")
    assert client.get("/patients/1/reports").status_code == 403


def test_diary_comment_is_linked_to_own_entry_and_visible_to_patient(client):
    login(client, "patient1")
    assert (
        post(
            client,
            "/patients/1/diary",
            day=date.today().isoformat(),
            wellbeing=5,
            sleep=8,
            adherence="none",
            note="Синтетическая запись",
        ).status_code
        == 200
    )
    did = rows(client, "SELECT id FROM diary")[0]["id"]
    assert (
        post(
            client, f"/patients/1/diary/{did}/comments", body="Подмена авторства"
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert (
        post(
            client, f"/patients/2/diary/{did}/comments", body="Чужая запись"
        ).status_code
        == 404
    )
    assert (
        post(
            client,
            f"/patients/1/diary/{did}/comments",
            body="Учебный комментарий специалиста",
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert "Учебный комментарий специалиста" in client.get("/patients/1/diary").text
    assert rows(client, "SELECT note FROM diary")[0]["note"] == "Синтетическая запись"


def test_private_question_endpoints_are_retired_without_exposing_patient_data(client):
    login(client, "patient1")
    for endpoint in [
        "/patients/1/messages",
        "/patients/1/questions",
        "/patients/1/questions/1/reply",
    ]:
        assert (
            post(client, endpoint, body="Учебный вопрос", persona="anna").status_code
            == 410
        )
    assert not rows(client, "SELECT * FROM messages")
    assert not rows(client, "SELECT * FROM specialist_questions")
    login(client, "patient2")
    assert post(client, "/patients/1/questions", body="Чужая запись").status_code == 403


def test_video_requires_safe_link_fallback_preparation_and_role(client):
    login(client, "doctor")
    data = dict(
        starts_at="2026-12-01T10:30",
        timezone_label="Москва (UTC+3)",
        url="https://example.org/synthetic-appointment",
        fallback="Учебный резервный способ связи",
    )
    for unsafe in [
        "javascript:alert(1)",
        "http://example.org/",
        "https://username:password@example.org/",
        "https://example.org/\nunsafe",
    ]:
        assert (
            post(
                client, "/patients/1/appointments", **(data | {"url": unsafe})
            ).status_code
            == 422
        )
    assert post(client, "/patients/1/appointments", **data).status_code == 200
    vid = rows(client, "SELECT id FROM appointments")[0]["id"]
    endpoint = f"/patients/1/appointments/{vid}/status"
    assert post(client, endpoint, intent="ready").status_code == 422
    assert post(client, endpoint, intent="completed").status_code == 409
    assert (
        post(
            client,
            endpoint,
            intent="ready",
            identity_checked="true",
            location_checked="true",
            privacy_checked="true",
            fallback_checked="true",
        ).status_code
        == 200
    )
    login(client, "patient1")
    page = client.get("/patients/1/contact").text
    assert data["url"] in page and data["fallback"] in page
    assert post(client, endpoint, intent="completed").status_code == 403
    login(client, "doctor")
    assert post(client, endpoint, intent="completed").status_code == 200
    assert "Открыть видеоприём" not in client.get("/patients/1/contact").text
    assert (
        post(
            client,
            endpoint,
            intent="ready",
            identity_checked="true",
            location_checked="true",
            privacy_checked="true",
            fallback_checked="true",
        ).status_code
        == 409
    )


def test_external_safety_review_does_not_create_fictional_answers(client):
    login(client, "doctor")
    assert (
        post(
            client,
            "/patients/1/safety-review",
            form_version="Учебная внешняя форма v1",
            source="Синтетический источник",
            notes="Служебная запись беседы",
            patient_summary="Согласованный учебный следующий шаг",
        ).status_code
        == 200
    )
    assert not rows(client, "SELECT * FROM answers")
    login(client, "patient1")
    page = client.get("/patients/1/safety").text
    assert "Согласованный учебный следующий шаг" in page
    assert "Служебная запись беседы" not in page
    assert (
        post(
            client,
            "/patients/1/safety-review",
            form_version="Подмена",
            source="Подмена",
            notes="Подмена",
            patient_summary="Подмена",
        ).status_code
        == 403
    )


def test_single_question_save_preserves_other_answers_history_and_conflicts(client):
    login(client, "patient1")
    aid = start(client)
    endpoint = f"/assessments/{aid}/save"
    first = save_data(
        client,
        aid,
        [2],
        intent="next",
        step_item=1,
        comment_1="Учебный комментарий",
        discuss_1="true",
    )
    assert client.post(endpoint, data=first).url.query == b"item=2&saved=1"
    assert client.post(endpoint, data=first).status_code == 200
    data = save_data(client, aid, [], intent="next", step_item=2, answer_2="declined")
    assert client.post(endpoint, data=data).status_code == 200
    assert rows(
        client, "SELECT value,state,comment,discuss FROM answers WHERE item='1'"
    )[0] == dict(value=2, state="answered", comment="Учебный комментарий", discuss=1)
    assert rows(client, "SELECT value,state FROM answers WHERE item='2'")[0] == dict(
        value=None, state="declined"
    )
    for i in [3, 4]:
        data = save_data(
            client,
            aid,
            [],
            intent="next" if i == 3 else "submit",
            step_item=i,
            **{f"answer_{i}": 1},
        )
        assert client.post(endpoint, data=data).status_code == 200
    assert rows(client, "SELECT status FROM assessments")[0]["status"] == "incomplete"
    stale = save_data(client, aid, [], intent="next", step_item=2, answer_2=0)
    assert client.post(endpoint, data=stale).status_code == 200
    assert (
        client.post(
            endpoint,
            data=stale | {"request_id": "another-synthetic-token", "answer_2": 3},
        ).status_code
        == 409
    )
    assert save(client, aid, [2, 0, 1, 1]).status_code == 200
    assert rows(client, "SELECT status FROM assessments")[0]["status"] == "completed"
    assert rows(client, "SELECT * FROM answer_history")


def test_phqads_assignment_package_is_atomic_idempotent_and_uses_same_session(client):
    login(client, "patient1")
    aid = start(client)
    assert save(client, aid, [1, 2, 1, 2]).status_code == 200
    sid = rows(client, "SELECT session_id FROM assessments")[0]["session_id"]
    login(client, "doctor")
    for _ in range(2):
        assert (
            post(
                client,
                "/patients/1/assign",
                session_id=sid,
                scale="phqads",
                due_day=date.today().isoformat(),
            ).status_code
            == 200
        )
    assigned = rows(
        client,
        "SELECT scale,session_id,assigned_by FROM assessments WHERE scale!='phq4' ORDER BY scale",
    )
    assert [a["scale"] for a in assigned] == ["gad7", "phq9"]
    assert all(a["session_id"] == sid and a["assigned_by"] for a in assigned)
    assert "пакет PHQ-ADS" in client.get("/patients/1/workspace/assessments").text
    login(client, "patient1")
    assert (
        post(
            client,
            "/patients/1/assign",
            session_id=sid,
            scale="phqads",
            due_day=date.today().isoformat(),
        ).status_code
        == 403
    )


def test_positive_ninth_answer_in_single_step_creates_event_without_final_score(client):
    login(client, "patient1")
    start(client)
    sid = rows(client, "SELECT id FROM assessment_sessions")[0]["id"]
    login(client, "doctor")
    aid = assign(client, sid, "phq9")
    login(client, "patient1")
    data = save_data(
        client,
        aid,
        [],
        intent="draft",
        step_item=9,
        answer_9=1,
        comment_9="Синтетическое пояснение",
    )
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    assert (
        rows(client, "SELECT status FROM assessments WHERE id=?", (aid,))[0]["status"]
        == "draft"
    )
    assert len(rows(client, "SELECT * FROM assessment_events")) == 1
    login(client, "doctor")
    card = client.get("/patients/1/workspace/conversation").text
    assert "Синтетическое пояснение" in card
    assert "Спасибо, что рассказали" in card
    assert rows(client, "SELECT value FROM answers")[0]["value"] == 1


@pytest.mark.parametrize(
    "section,fields",
    [
        ("profile", {"biography": "Синтетическая биография"}),
        ("notes", {"body": "Учебная заметка"}),
    ],
)
def test_consent_withdrawal_stops_new_patient_portal_writes(client, section, fields):
    login(client, "patient1")
    assert post(client, "/consent").status_code == 200
    assert post(client, "/patients/1/" + section, **fields).status_code == 403
    assert client.get("/faq").status_code == 200
