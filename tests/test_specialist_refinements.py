"""Patient activity, optional links for hours, mandatory links for appointments,
and automatic PHQ-4 follow-ups over real HTTP/SQLite writes.
"""

import re
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token
from tests.test_specialist_upgrade import calendar_form, records
from tests.test_workflows import start, save, rows


def test_activity_orders_patients_and_keeps_private_drafts_private(client):
    login(client, "patient2")
    assert (
        client.post(
            "/patients/2/notes", data=dict(csrf=token(client), body="PRIVATE_FREE_TEXT")
        ).status_code
        == 200
    )
    login(client, "doctor")
    html = client.get("/doctor").text
    assert html.index("patient-activity") < html.index('id="patient-cards"')
    cards = html.split('id="patient-cards"')[1]
    assert cards.index('/patients/2"') < cards.index('/patients/1"')
    assert "Добавлена заметка" in html and "PRIVATE_FREE_TEXT" not in html
    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/diary",
            data=dict(
                csrf=token(client),
                day=date.today().isoformat(),
                wellbeing=0,
                sleep=8,
                adherence="none",
                note="PRIVATE_DIARY",
            ),
        ).status_code
        == 200
    )
    login(client, "doctor")
    html = client.get("/doctor").text
    cards = html.split('id="patient-cards"')[1]
    assert cards.index('/patients/1"') < cards.index('/patients/2"')
    assert "Заполнен дневник" in html and "PRIVATE_DIARY" not in html
    # A doctor's own reply must not move a patient ahead of the latest patient action.
    assert (
        client.post(
            "/patients/1/diary/1/comments",
            data=dict(csrf=token(client), body="Врач ответил"),
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    html = client.get("/doctor").text
    assert "Демо-пациент Б" not in html
    assert client.get("/doctor").headers["cache-control"] == "no-store"
    login(client, "patient1")
    assert client.get("/doctor").status_code == 403


@pytest.mark.parametrize(
    "values,expected",
    [
        ([0, 0, 1, 1], set()),
        ([0, 0, 1, 2], {"phq9"}),
        ([1, 2, 0, 0], {"gad7"}),
        ([1, 2, 3, 3], {"gad7", "phq9"}),
    ],
)
def test_followups_only_after_complete_submission_without_copying_answers(
    client, values, expected
):
    login(client, "patient1")
    aid = start(client)
    assert save(client, aid, values, intent="draft").status_code == 200
    assert {a["scale"] for a in records(client, "assessments")} == {"phq4"}
    assert save(client, aid, values).status_code == 200
    forms = records(client, "assessments")
    assert {a["scale"] for a in forms} == expected | {"phq4"}
    source = next(a for a in forms if a["id"] == aid)
    assert all(
        a["session_id"] == source["session_id"] and a["patient_id"] == 1 for a in forms
    )
    assert not rows(client, "SELECT 1 FROM answers WHERE assessment_id!=?", (aid,))
    # Resubmission is idempotent and does not replace existing follow-up answers.
    assert save(client, aid, values).status_code == 200
    assert len(records(client, "assessments")) == len(forms)
    login(client, "doctor")
    if expected == {"gad7", "phq9"}:
        # Explicitly adding the package to an automatically extended session
        # updates its due date without duplicating/re-attributing its forms.
        assert (
            client.post(
                "/patients/1/assign",
                data=dict(
                    csrf=token(client),
                    session_id=source["session_id"],
                    scale="phqads",
                    due_day=date.today().isoformat(),
                ),
            ).status_code
            == 200
        )
        assert len(records(client, "assessments")) == len(forms)
        assert all(a["assigned_by"] is None for a in records(client, "assessments"))
    html = client.get("/patients/1/workspace/assessments").text
    for removed in (
        "Предложения для уточнения",
        "Выбрать обследование",
        "Отмеченные ответы и действия",
        "PHQ-4 · новый период",
    ):
        assert removed not in html
    assert "Пройдена анкета PHQ-4" in client.get("/doctor").text


def test_subscales_are_independent_and_restart_reconciles_existing_completed_once(
    client,
):
    login(client, "patient1")
    aid = start(client)
    assert save(client, aid, [3, 3, "deferred", 3]).status_code == 200
    assert {a["scale"] for a in records(client, "assessments")} == {"phq4", "gad7"}
    # Represents a completed PHQ-4 from an older application version.
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE answers SET value=3,state='answered' WHERE assessment_id=? AND item='3'",
            (aid,),
        )
        c.execute("UPDATE assessments SET status='completed' WHERE id=?", (aid,))
    for _ in range(2):
        with TestClient(create_app(client.app.state.db_path)) as restarted:
            assert len(records(restarted, "assessments")) == 3
            assert len(records(restarted, "answers")) == 4
    assert {a["patient_id"] for a in records(client, "assessments")} == {1}


def test_free_hours_without_link_and_appointment_requires_link_atomically(client):
    login(client, "doctor")
    form = calendar_form(client, event_kind="free", url="", fallback="")
    assert client.post("/doctor/calendar/events", data=form).status_code == 200
    slot = records(client, "consultation_slots")[0]
    assert slot["url"] == "" and not records(client, "appointments")
    bad = calendar_form(
        client,
        event_kind="appointment",
        patient_id="1",
        time="13:00",
        url="",
        fallback="",
    )
    assert client.post("/doctor/calendar/events", data=bad).status_code == 422
    assert len(records(client, "consultation_slots")) == 1
    assert not records(client, "appointments")
    assert (
        client.post(
            "/patients/1/consultations",
            data=dict(csrf=token(client), slot_id=slot["id"]),
        ).status_code
        == 422
    )
    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/consultations",
            data=dict(csrf=token(client), slot_id=slot["id"]),
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/patients/1/consultations",
            data=dict(
                csrf=token(client),
                slot_id=slot["id"],
                url="https://example.org",
                fallback="x",
            ),
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert (
        client.post(
            "/patients/1/consultations",
            data=dict(
                csrf=token(client),
                slot_id=slot["id"],
                url="https://example.org/room",
                fallback="Резервный контакт",
            ),
        ).status_code
        == 200
    )
    visit = records(client, "appointments")[0]
    assert visit["url"] == "https://example.org/room" and visit["slot_id"] == slot["id"]
    assert records(client, "consultation_slots")[0]["status"] == "booked"


def test_forum_buttons_and_clean_calendar(client):
    login(client, "doctor")
    html = client.get("/doctor/forums").text
    assert '#forums"' in html and '#people"' in html
    assert len(re.findall(r'<a class="button" href="/doctor/(?:forum|faq)"', html)) == 2
    html = client.get("/doctor/calendar").text
    assert (
        'data-calendar-add="free"' in html and 'data-calendar-add="appointment"' in html
    )
    assert "data-calendar-time=" in html and 'T23:00"' in html and 'T00:00"' in html
    assert "События из анкет и сообщения пациентов" not in html


def test_comments_and_completed_materials_appear_in_activity(client):
    login(client, "patient2")
    aid = start(client)
    assert (
        save(
            client, aid, [0, 0, 0, 0], intent="draft", comment_1="PRIVATE_COMMENT"
        ).status_code
        == 200
    )
    login(client, "doctor")
    html = client.get("/doctor").text
    assert "Добавлено пояснение к анкете PHQ-4" in html
    assert "PRIVATE_COMMENT" not in html
    with connect(client.app.state.db_path) as c:
        resource = c.execute(
            "SELECT id FROM resource_items ORDER BY id LIMIT 1"
        ).fetchone()[0]
    assert (
        client.post(
            f"/doctor/resources/{resource}/assign",
            data=dict(csrf=token(client), patient_id="2"),
        ).status_code
        == 200
    )
    assignment_id = records(client, "resource_assignments")[0]["id"]
    login(client, "patient2")
    assert (
        client.post(
            f"/material-assignments/{assignment_id}/status",
            data=dict(csrf=token(client), status="read"),
        ).status_code
        == 200
    )
    login(client, "doctor")
    html = client.get("/doctor").text
    assert "Прочитан материал" in html
    assert 'href="/doctor/resources#assignments"' in html
