from datetime import date
from fastapi.testclient import TestClient
import pytest
from app.main import create_app
from app.storage import connect
from app.services.materials import valid_url
from tests.helpers import login, token


def test_real_resource_assignment_read_status_and_restart(client):
    login(client, "doctor")
    assert (
        client.get("/doctor/resources").text.count('class="card resource-card"') == 12
    )
    data = dict(
        csrf=token(client),
        title="Своя статья",
        url="https://example.org/article",
        description="Материал для обсуждения",
    )
    assert client.post("/doctor/resources", data=data).status_code == 200
    with connect(client.app.state.db_path) as c:
        rid = c.execute(
            "SELECT id FROM resource_items WHERE title='Своя статья'"
        ).fetchone()[0]
    payload = dict(
        csrf=token(client),
        patient_id="1",
        note="Обсудим на встрече",
        due_day=date.today().isoformat(),
    )
    assert (
        client.post(f"/doctor/resources/{rid}/assign", data=payload).status_code == 200
    )
    assert (
        client.post(f"/doctor/resources/{rid}/assign", data=payload).status_code == 200
    )
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM resource_assignments").fetchone()[0] == 1
    login(client, "patient1")
    page = client.get("/me/materials")
    assert "Своя статья" in page.text and "Обсудим на встрече" in page.text
    assert client.get("/doctor/resources").status_code == 403
    r = client.get("/material-assignments/1/open", follow_redirects=False)
    assert (
        r.status_code == 303 and r.headers["location"] == "https://example.org/article"
    )
    assert (
        client.post(
            "/material-assignments/1/status",
            data=dict(csrf=token(client), status="read"),
        ).status_code
        == 200
    )
    login(client, "patient2")
    assert "Своя статья" not in client.get("/me/materials").text
    assert (
        client.get("/material-assignments/1/open", follow_redirects=False).status_code
        == 403
    )
    assert (
        client.post(
            "/material-assignments/1/status",
            data=dict(csrf=token(client), status="read"),
        ).status_code
        == 403
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert "Прочитано" in restarted.get("/doctor/resources").text
        assert (
            restarted.post(
                "/material-assignments/1/status",
                data=dict(csrf=token(restarted), status="read"),
            ).status_code
            == 403
        )
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM resource_items").fetchone()[0] == 13
        assert [
            r[0] for r in c.execute("SELECT kind FROM resource_events ORDER BY id")
        ] == ["assigned", "opened", "read"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "javascript:alert(1)",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://192.168.1.1/a",
        "https://user:secret@example.org/a",
        "https://example.org:8000/a",
        "https://example.org/\nfoo",
    ],
)
def test_resource_urls_reject_local_or_active_content(url):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        valid_url(url)


def test_resources_cannot_be_assigned_to_foreign_patient_or_created_by_patient(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой специалист')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    assert (
        client.post(
            "/doctor/resources/1/assign", data=dict(csrf=token(client), patient_id=2)
        ).status_code
        == 403
    )
    login(client, "patient1")
    assert (
        client.post(
            "/doctor/resources",
            data=dict(
                csrf=token(client), title="Не назначение", url="https://example.org/a"
            ),
        ).status_code
        == 403
    )
