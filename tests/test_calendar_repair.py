"""Long meetings, return navigation and dates without changing existing records."""

import html
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from tests.helpers import login, token
from tests.test_specialist_upgrade import calendar_form, records
from tests.test_specialist_requests import end_form
from app.storage import connect, now


def back_url(page):
    return html.unescape(re.search(r'data-error-back href="([^"]+)"', page).group(1))


def test_long_and_short_intervals_book_once_and_show_every_covered_day(client):
    login(client, "doctor")
    data = end_form(
        client,
        date="2030-01-07",
        time="09:00",
        end_date="2030-01-12",
        end_time="18:35",
        event_kind="appointment",
        patient_id="1",
    )
    for _ in range(2):
        assert client.post("/doctor/calendar/events", data=data).status_code == 200
    visit = records(client, "appointments")[0]
    assert visit["ends_at"] == "2030-01-12T15:35:00+00:00"
    assert len(records(client, "appointments")) == 1
    calendar = client.get("/doctor/calendar?week=2030-01-07").text
    assert calendar.count('class="calendar-event event-planned"') == 6
    assert "00:00–24:00" in calendar and "00:00–18:35" in calendar
    assert "07.01.2030 09:00 — 12.01.2030 18:35" in calendar
    assert (
        client.post(
            "/doctor/calendar/events",
            data=end_form(client, date="2030-01-10", time="12:00", end_time="12:01"),
        ).status_code
        == 409
    )
    short = end_form(client, date="2030-01-13", time="12:00", end_time="12:01")
    assert client.post("/doctor/calendar/events", data=short).status_code == 200
    legacy = calendar_form(
        client, date="2030-02-01", time="08:00", duration_minutes="5760"
    )
    assert client.post("/doctor/calendar/events", data=legacy).status_code == 200
    login(client, "patient1")
    assert client.post("/doctor/calendar/events", data=data).status_code == 403
    assert client.get("/doctor/calendar").status_code == 403


def test_long_interval_cannot_skip_unknown_legacy_meeting_in_the_middle(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        actor = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[
            0
        ]
        c.execute(
            "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(1,?,'2030-01-09T13:00','Europe/Moscow','https://example.org','Резерв',?)",
            (actor, now()),
        )
    data = end_form(
        client, date="2030-01-07", time="08:00", end_date="2030-01-12", end_time="18:00"
    )
    assert client.post("/doctor/calendar/events", data=data).status_code == 409
    with connect(client.app.state.db_path) as c:
        c.execute("UPDATE appointments SET timezone_label='Неизвестный пояс'")
    assert client.post("/doctor/calendar/events", data=data).status_code == 409
    assert not records(client, "consultation_slots")


def test_unrepresentable_end_date_is_a_validation_error_without_writes(client):
    login(client, "doctor")
    data = calendar_form(client, duration_minutes=str(10**30))
    response = client.post("/doctor/calendar/events", data=data)
    assert response.status_code == 422
    assert not records(client, "appointments") and not records(
        client, "consultation_slots"
    )


def test_error_returns_to_same_calendar_context_and_does_not_save(client):
    login(client, "doctor")
    source = "/doctor/calendar?week=2030-01-07&view=day&patient_id=2&timezone=UTC&create=appointment&event_date=2030-01-08&event_time=14%3A00#calendar-editor"
    data = end_form(
        client,
        date="2030-01-08",
        time="14:00",
        end_date="2030-01-08",
        end_time="13:00",
        patient_id="2",
        event_kind="appointment",
        return_to=source,
    )
    response = client.post("/doctor/calendar/events", data=data)
    assert response.status_code == 422 and back_url(response.text) == source
    returned = client.get(back_url(response.text))
    assert returned.url.path == "/doctor/calendar"
    assert parse_qs(returned.url.query.decode())["patient_id"] == ["2"]
    assert 'name="date" value="2030-01-08"' in returned.text
    assert not records(client, "appointments") and not records(
        client, "consultation_slots"
    )


def test_error_return_prefers_source_tab_and_remembers_last_html_page(client):
    login(client, "doctor")
    source = "/patients/1/diary"
    client.get(source)
    # Another tab need not become the return target for this form.
    client.get("/doctor/forums")
    response = client.post(
        "/patients/1/diary/999/comments",
        data=dict(csrf=token(client), body="Вымышленный комментарий"),
        headers={"referer": "http://testserver" + source},
    )
    assert response.status_code == 404 and back_url(response.text) == source
    client.get("/doctor/calendar?week=2030-01-07&patient_id=1")
    response = client.post("/doctor/calendar/events", data={})
    assert response.status_code == 403
    assert back_url(response.text).startswith("/doctor/calendar?week=2030-01-07")


@pytest.mark.parametrize(
    "target",
    [
        "https://outside.invalid/x",
        "//outside.invalid/x",
        "/%2f%2foutside.invalid",
        "/%5coutside.invalid",
        "/doctor%0d%0aLocation:bad",
        "javascript:alert(1)",
    ],
)
def test_error_return_rejects_external_and_malformed_targets(client, target):
    login(client, "doctor")
    client.get("/doctor/forums")
    response = client.post(
        "/doctor/calendar/events",
        data=dict(csrf="wrong", return_to=target),
        headers={"referer": "https://outside.invalid/x"},
    )
    assert response.status_code == 403
    assert back_url(response.text) == "/doctor/forums"


def test_validation_and_unexpected_errors_have_local_back_link(tmp_path):
    app = create_app(tmp_path / "synthetic.db")

    @app.get("/test-error-only")
    def synthetic_error():
        raise RuntimeError("PRIVATE_DIAGNOSTIC_TEXT")

    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, "patient1")
        source = "/patients/1/diary"
        client.get(source)
        response = client.post(
            source,
            data=dict(
                csrf=token(client),
                day="wrong",
                wellbeing="wrong",
                sleep="wrong",
                adherence="none",
            ),
            headers={"referer": "http://testserver" + source},
        )
        assert response.status_code == 422 and back_url(response.text) == source
        response = client.get(
            "/test-error-only", headers={"referer": "http://testserver" + source}
        )
        assert response.status_code == 500 and back_url(response.text) == source
        assert "PRIVATE_DIAGNOSTIC_TEXT" not in response.text
