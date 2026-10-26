"""Patient requests, explicit end times and durable constructor formats."""

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token
from tests.test_intake import save, send
from tests.test_workflows import start, save as save_scale
from tests.test_diary_questions import defs
from tests.test_specialist_upgrade import calendar_form, records


def request_info(client, topic="medicines", pid=1, **changes):
    return client.post(
        f"/patients/{pid}/information-requests",
        data=dict(csrf=token(client), topic=topic) | changes,
    )


def test_request_is_scoped_idempotent_and_private_until_submission(client):
    login(client, "doctor")
    for _ in range(2):
        assert request_info(client).status_code == 200
    assert len(records(client, "patient_information_requests")) == 1
    assert request_info(client, "allergies").status_code == 200
    assert request_info(client, "invalid").status_code == 422
    assert request_info(client, csrf="wrong").status_code == 403
    assert "Запрос отправлен" in client.get("/patients/1/profile").text
    login(client, "patient2")
    assert "По просьбе врача" not in client.get("/patients/2/profile/about").text
    assert request_info(client).status_code == 403
    login(client, "patient1")
    assert request_info(client).status_code == 403
    assert "По просьбе врача" in client.get("/patients/1/profile/about").text
    assert "По просьбе врача" in client.get("/patients/1/profile/assessments").text
    assert save(client, "medicines", a_current="Нет").status_code == 200
    requests = records(client, "patient_information_requests")
    assert requests[0]["answered_at"] and requests[1]["answered_at"] is None
    login(client, "doctor")
    assert "Ответ по запросу врача" in client.get("/patients/1/profile").text
    login(client, "patient1")
    requests = records(client, "patient_information_requests")
    assert requests[0]["answered_at"] and requests[0]["submission_id"]
    assert requests[1]["answered_at"] is None  # Medicines are not an allergy answer.
    assert save(client, "medicines", a_reactions="Нет").status_code == 200
    assert all(
        r["answered_at"] for r in records(client, "patient_information_requests")
    )
    assert len(records(client, "intake_submissions")) == 2
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert "По просьбе врача" not in restarted.get("/patients/1/profile/about").text
        assert len(records(restarted, "patient_information_requests")) == 2
    login(client, "doctor")
    assert request_info(client).status_code == 200
    assert len(records(client, "patient_information_requests")) == 3
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    assert request_info(client, pid=2).status_code == 403


def test_unrelated_submission_does_not_close_request_and_goals_wait_for_scales(client):
    login(client, "patient1")
    aid = start(client)
    login(client, "doctor")
    assert request_info(client, "conditions").status_code == 200
    assert request_info(client, "goals").status_code == 200
    login(client, "patient1")
    assert (
        save(client, "personal", a_preferred_name="Вымышленное имя").status_code == 200
    )
    assert send(client).status_code == 200
    assert all(
        r["answered_at"] is None
        for r in records(client, "patient_information_requests")
    )
    assert save(client, "goals", a_goal_one="Обсудить самочувствие").status_code == 200
    assert send(client).status_code == 200
    assert records(client, "patient_information_requests")[1]["answered_at"] is None
    assert save_scale(client, aid, [0, 0, 0, 0]).status_code == 200
    requests = records(client, "patient_information_requests")
    assert requests[0]["answered_at"] is None and requests[1]["answered_at"]


def test_patient_can_resend_unchanged_current_information_without_fabricating_a_new_version(
    client,
):
    login(client, "patient1")
    assert save(client, "medicines", a_current="Нет").status_code == 200
    assert send(client).status_code == 200
    login(client, "doctor")
    assert request_info(client).status_code == 200
    login(client, "patient1")
    assert records(client, "patient_information_requests")[0]["answered_at"] is None
    assert send(client).status_code == 200
    assert records(client, "patient_information_requests")[0]["answered_at"]
    assert len(records(client, "intake_submissions")) == 1


def end_form(client, **changes):
    data = calendar_form(client, **changes)
    data.pop("duration_minutes")
    return data


def test_explicit_end_times_keep_utc_duration_conflicts_and_legacy_requests(client):
    login(client, "doctor")
    data = end_form(
        client, time="12:15", end_time="14:05", event_kind="free", url="", fallback=""
    )
    assert client.post("/doctor/calendar/events", data=data).status_code == 200
    slot = records(client, "consultation_slots")[0]
    from datetime import datetime

    assert (
        datetime.fromisoformat(slot["ends_at"])
        - datetime.fromisoformat(slot["starts_at"])
    ).total_seconds() == 110 * 60
    assert (
        client.post(
            "/doctor/calendar/events",
            data=end_form(client, time="13:00", end_time="13:30"),
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/doctor/calendar/events",
            data=end_form(client, time="14:00", end_time="13:00"),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/doctor/calendar/events",
            data=end_form(client, time="14:10", end_time="20:10"),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/doctor/calendar/events",
            data=calendar_form(client, time="16:00", end_time="17:00"),
        ).status_code
        == 422
    )
    night = end_form(
        client,
        time="23:30",
        end_time="00:15",
        ends_next_day="yes",
        patient_id="1",
        event_kind="appointment",
    )
    assert client.post("/doctor/calendar/events", data=night).status_code == 200
    visit = records(client, "appointments")[0]
    assert visit["ends_at"].endswith("21:15:00+00:00")
    assert (
        client.post(
            "/doctor/calendar/events", data=calendar_form(client, time="20:30")
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert client.post("/doctor/calendar/events", data=night).status_code == 403


@pytest.mark.parametrize(
    "condition",
    ["sleep_lte:25", "wellbeing_lte:11", "wellbeing_lte:", "wrong", "sleep_lte:nan"],
)
def test_constructor_rejects_invalid_combined_conditions(client, condition):
    login(client, "doctor")
    data = dict(
        csrf=token(client),
        question_builder="yes",
        title="Вопрос",
        kind="text",
        category="activity",
        condition=condition,
    )
    assert client.post("/patients/1/diary/metrics", data=data).status_code == 422
    assert not records(client, "diary_metrics")


def test_constructor_formats_condition_and_old_order_round_trip(client):
    login(client, "doctor")
    data = dict(
        csrf=token(client),
        question_builder="yes",
        title="Что помогло?",
        kind="choice",
        category="activity",
        options="Прогулка\nОтдых",
        condition="sleep_lte:7.25",
    )
    assert client.post("/patients/1/diary/metrics", data=data).status_code == 200
    question = defs(client)[0]
    assert question["threshold"] == 7.25 and question["kind"] == "choice"
    page = client.get("/patients/1/diary/questions/1/edit").text
    assert 'value="sleep_lte:7.25" selected' in page
    assert 'name="position"' not in page and 'name="threshold"' not in page
    # Removing the order field must not reorder an existing custom definition.
    with connect(client.app.state.db_path) as c:
        import json

        row = c.execute(
            "SELECT definition FROM diary_question_versions WHERE metric_id=1 AND version=1"
        ).fetchone()
        original = json.loads(row[0])
        original["position"] = 99
        c.execute(
            "UPDATE diary_question_versions SET definition=? WHERE metric_id=1 AND version=1",
            (json.dumps(original),),
        )
    data.update(kind="text", revision="1", condition="always")
    assert (
        client.post("/patients/1/diary/questions/1/save", data=data).status_code == 200
    )
    assert defs(client)[0]["position"] == 99
    assert defs(client)[0]["kind"] == "text" and defs(client)[0]["options"] == []
    assert len(records(client, "diary_question_versions")) == 2


def test_explicit_demo_reset_handles_request_foreign_keys_and_preserves_other_card(
    client, tmp_path
):
    login(client, "doctor")
    assert request_info(client).status_code == 200
    login(client, "patient1")
    assert save(client, "medicines", a_current="Нет").status_code == 200
    assert send(client).status_code == 200
    assert records(client, "patient_information_requests")[0]["submission_id"]
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO patients(id,name,doctor_id) VALUES(3,'Сохраняемая вымышленная карта',1)"
        )
        actor = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[
            0
        ]
        c.execute(
            "INSERT INTO patient_information_requests(patient_id,requested_by,topic,baseline_hash,created_at) VALUES(3,?,'conditions','test','2026-10-24')",
            (actor,),
        )
    from scripts.reset_demo_forms import reset

    reset(client.app.state.db_path, apply=True, destination=tmp_path / "backup.db")
    assert {
        r["patient_id"] for r in records(client, "patient_information_requests")
    } == {3}
    assert (tmp_path / "backup.db").is_file()
