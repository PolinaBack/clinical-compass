"""Calendar-aware progress from saved diary dates and patient-only presentation."""

from datetime import date, timedelta
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from app.services import patient_journey as journey
from app.services.clinical_forms import today
from tests.helpers import login, token
from tests.test_diary_library_offline import store


def insert(c, pid, day, wellbeing=0, adherence="none"):
    c.execute(
        "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(?,?,?,0,?,'PRIVATE_DIARY_NOTE',0)",
        (pid, day, wellbeing, adherence),
    )


@pytest.mark.parametrize(
    "month,season",
    [
        (1, "winter"),
        (2, "winter"),
        (3, "spring"),
        (4, "spring"),
        (5, "spring"),
        (6, "summer"),
        (7, "summer"),
        (8, "summer"),
        (9, "autumn"),
        (10, "autumn"),
        (11, "autumn"),
        (12, "winter"),
    ],
)
def test_four_seasons_follow_current_moscow_calendar(month, season):
    assert journey.season_for(date(2026, month, 1)) == season


def test_saved_dates_are_unique_not_scores_or_streaks_and_future_invalid_dates_do_not_count(
    store,
):
    _, path, _ = store
    reference = date(2026, 10, 26)
    with connect(path) as c:
        for day, value, state in [
            ("2026-10-01", 0, "none"),
            ("2026-10-01", 10, "taken"),
            ("2026-10-25", 4, "missed"),
            ("2026-10-27", 0, "none"),
            ("2026-02-30", 0, "none"),
            ("unknown", 0, "none"),
        ]:
            insert(c, 1, day, value, state)
        for index in range(15):
            insert(c, 2, (reference - timedelta(days=index)).isoformat())
        original = c.execute("SELECT * FROM diary ORDER BY id").fetchall()
        result = journey.progress(c, 1, reference)
        assert result["days"] == result["unique_days"] == 2
        assert result["percent"] == pytest.approx(2 / 90 * 100)
        assert result["season"] == "autumn"
        assert c.execute("SELECT * FROM diary ORDER BY id").fetchall() == original
        assert "PRIVATE_DIARY_NOTE" not in json.dumps(result)


def test_days_advance_in_equal_steps_gaps_and_season_change_preserve_progress_and_ninety_finishes(
    store,
):
    _, path, _ = store
    reference = date(2026, 11, 30)
    with connect(path) as c:
        initial = journey.progress(c, 1, reference)
        assert initial["days"] == 0 and initial["percent"] == 0
        positions = []
        for index in range(1, 4):
            insert(c, 1, (reference - timedelta(days=index * 3)).isoformat())
            positions.append(journey.progress(c, 1, reference)["percent"])
        assert positions[1] - positions[0] == pytest.approx(
            positions[2] - positions[1], abs=0.00002
        )
        autumn = journey.progress(c, 1, reference)
        winter = journey.progress(c, 1, date(2026, 12, 1))
        assert autumn["percent"] == winter["percent"] and winter["season"] == "winter"
        for index in range(92):
            insert(c, 1, (reference - timedelta(days=20 + index)).isoformat())
        done = journey.progress(c, 1, reference)
        assert done["days"] == 90 and done["percent"] == 100 and done["complete"]
        assert done["unique_days"] == 95


def save(client, day, **changes):
    data = dict(csrf=token(client), day=day, wellbeing="0", sleep="0", adherence="none")
    data.update(changes)
    return client.post("/patients/1/diary", data=data)


def test_real_save_and_duplicate_day_update_progress_on_all_patient_tabs_and_restart(
    client,
):
    login(client, "patient1")
    current = today()
    endpoint = "/api/me/diary-journey"
    assert client.get(endpoint).json()["days"] == 0
    assert save(client, current.isoformat()).status_code == 200
    assert (
        save(
            client, current.isoformat(), wellbeing="10", adherence="missed"
        ).status_code
        == 200
    )
    assert client.get(endpoint).json()["days"] == 1
    assert save(client, (current - timedelta(days=8)).isoformat()).status_code == 200
    assert client.get(endpoint).json()["days"] == 2
    for path in [
        "/patients/1/diary",
        "/patients/1/profile",
        "/patients/1/profile/assessments",
        "/patients/1/results",
        "/patients/1/profile/extracts",
        "/me/materials",
        "/settings",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.text.index("data-diary-journey") < response.text.index(
            'aria-label="Навигация пациента"'
        )
        assert 'aria-valuenow="2"' in response.text and "2 из 90 дней" in response.text
        assert response.headers["Cache-Control"] == "no-store"
    assert save(client, (current + timedelta(days=1)).isoformat()).status_code == 422
    assert client.get(endpoint).json()["days"] == 2
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert restarted.get(endpoint).json()["days"] == 2


def test_progress_api_is_own_account_only_and_public_pages_doctor_and_other_patient_keep_their_own_view(
    client,
):
    endpoint = "/api/me/diary-journey"
    assert client.get(endpoint).status_code == 401
    login(client, "patient1")
    assert save(client, today().isoformat()).status_code == 200
    own = client.get(endpoint + "?patient_id=2")
    assert own.status_code == 200 and own.json()["days"] == 1
    assert own.headers["Cache-Control"] == "no-store"
    assert "PRIVATE_DIARY_NOTE" not in own.text
    for path in ["/", "/login/patient", "/login/specialist"]:
        html = client.get(path).text
        assert "data-diary-journey" not in html and "patient_journey.css" not in html
    login(client, "patient2")
    assert client.get(endpoint).json()["days"] == 0
    assert 'aria-valuenow="0"' in client.get("/patients/2/diary").text
    assert client.get("/patients/1/diary").status_code == 403
    login(client, "doctor")
    assert client.get(endpoint).status_code == 403
    assert "data-diary-journey" not in client.get("/patients/1/diary").text


def test_moscow_diary_date_can_cross_utc_midnight_and_minimal_view_keeps_the_counter(
    client, monkeypatch
):
    import app.main as main

    local_day = date.today() + timedelta(days=1)
    monkeypatch.setattr(main, "clinical_today", lambda: local_day)
    monkeypatch.setattr(journey, "today", lambda: local_day)
    login(client, "patient1")
    assert (
        f'name="day" value="{local_day.isoformat()}"'
        in client.get("/patients/1/diary").text
    )
    assert save(client, local_day.isoformat()).status_code == 200
    assert client.get("/api/me/diary-journey").json()["days"] == 1
    settings = client.post(
        "/settings/interface",
        data=dict(csrf=token(client), revision="0", minimal="true"),
    )
    assert settings.status_code == 200
    html = client.get("/patients/1/diary").text
    assert "minimal-ui" in html and 'aria-valuenow="1"' in html
