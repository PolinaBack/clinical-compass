"""HTTP sessions, patient visibility and printable document routes."""

import secrets
from fastapi.testclient import TestClient
from app.main import create_app
from app.services import patient_extracts as service
from app.storage import connect
from tests.helpers import login, token
from tests.test_patient_extracts_direct import form_for


def create(client):
    response = client.post(
        "/patients/1/profile/extracts/new",
        data=dict(csrf=token(client), client_token=secrets.token_urlsafe(20)),
    )
    assert response.status_code == 200, response.text
    with connect(client.app.state.db_path) as c:
        return c.execute("SELECT max(id) FROM patient_extracts").fetchone()[0]


def current(client, eid):
    with connect(client.app.state.db_path) as c:
        user = dict(
            c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        )
        return service.read(c, client.app.state.db_path, user, 1, eid, edit=True)


def post(client, eid, form):
    return client.post(
        f"/patients/1/profile/extracts/{eid}/save", data={**form, "csrf": token(client)}
    )


def test_document_editor_print_publication_and_private_draft_in_real_sessions(client):
    login(client, "doctor")
    eid = create(client)
    assert "Открыть выписки" in client.get("/patients/1/profile").text
    original = current(client, eid)
    bad = post(client, eid, form_for(original, intent="print"))
    assert bad.status_code == 422 and 'name="reviewed"' in bad.text
    assert (
        post(
            client,
            eid,
            form_for(
                original,
                intent="publish",
                reviewed="yes",
                identity_full_name="Тестовый пациент",
                conclusion="ОПУБЛИКОВАННЫЙ ТЕКСТ",
            ),
        ).status_code
        == 200
    )
    assert (
        post(
            client,
            eid,
            form_for(current(client, eid), conclusion="ПРИВАТНОЕ ИЗМЕНЕНИЕ"),
        ).status_code
        == 200
    )
    login(client, "patient1")
    listing = client.get("/patients/1/profile/extracts")
    assert listing.status_code == 200 and "Медицинское заключение" in listing.text
    html = client.get(f"/patients/1/profile/extracts/{eid}").text
    assert "ОПУБЛИКОВАННЫЙ ТЕКСТ" in html and "ПРИВАТНОЕ ИЗМЕНЕНИЕ" not in html
    assert client.get(f"/patients/1/profile/extracts/{eid}/edit").status_code == 403
    assert (
        client.get(f"/patients/1/profile/extracts/{eid}/print?revision=3").status_code
        == 404
    )
    login(client, "doctor")
    printed = client.get(f"/patients/1/profile/extracts/{eid}/print?revision=2")
    assert printed.status_code == 200 and "data-print-document" in printed.text
    assert "ОБРАЗЕЦ · НЕ ДЕЙСТВИТЕЛЕН" in printed.text


def test_patient_role_foreign_patient_csrf_and_no_implicit_publication(client):
    login(client, "doctor")
    eid = create(client)
    assert (
        client.post(
            f"/patients/1/profile/extracts/{eid}/save", data=dict(csrf="wrong")
        ).status_code
        == 403
    )
    login(client, "patient1")
    assert not client.get("/patients/1/profile/extracts").context["documents"]
    assert (
        client.post(
            "/patients/1/profile/extracts/new",
            data=dict(csrf=token(client), client_token=secrets.token_urlsafe(20)),
        ).status_code
        == 403
    )
    assert client.get(f"/patients/1/profile/extracts/{eid}").status_code == 404
    login(client, "patient2")
    assert client.get("/patients/1/profile/extracts").status_code == 403
    assert client.get(f"/patients/1/profile/extracts/{eid}/print").status_code == 403


def test_two_clinician_sessions_keep_independent_csrf_and_do_not_overwrite_stale_document(
    client,
):
    login(client, "doctor")
    eid = create(client)
    old = current(client, eid)
    with TestClient(create_app(client.app.state.db_path)) as other:
        login(other, "doctor")
        assert client.get(f"/patients/1/profile/extracts/{eid}/edit").status_code == 200
        assert other.get(f"/patients/1/profile/extracts/{eid}/edit").status_code == 200
        assert (
            post(client, eid, form_for(old, conclusion="Первое окно")).status_code
            == 200
        )
        stale = post(
            other, eid, form_for(old, conclusion="Второе окно, текст для копирования")
        )
        assert (
            stale.status_code == 409
            and "Второе окно, текст для копирования" in stale.text
        )
        assert current(client, eid)["data"]["conclusion"] == "Первое окно"


def test_root_keeps_public_landing_and_extract_styles_are_scoped(client):
    login(client, "doctor")
    home = client.get("/")
    assert "Не обязательно" in home.text and "patient_extracts.css" not in home.text
    eid = create(client)
    assert (
        "patient_extracts.css"
        in client.get(f"/patients/1/profile/extracts/{eid}/edit").text
    )
    css = client.get("/static/patient_extracts.css")
    assert css.status_code == 200 and "@page{size:A4" in css.text
