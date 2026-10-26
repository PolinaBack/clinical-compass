"""End-to-end document request lifecycle on temporary databases, including files."""

from datetime import timedelta
import json
import secrets

import pytest
from app.services import extract_requests as requests, patient_extracts as documents
from app.services.clinical_forms import today
from app.storage import connect
from tests.helpers import login, token, TEST_HASH, TEST_PASSWORD
from tests.test_patient_extracts_direct import form_for
from tests.test_patient_extracts_http import current as current_document


def new_form(**changes):
    data = dict(
        client_token=secrets.token_urlsafe(24),
        kind="certificate",
        full_name="Пациент из запроса",
        birth_date="2000-01-01",
        contact="patient@example.test",
        representative="",
        purpose="education",
        purpose_other="",
        due="next_visit",
        due_date="",
        comment="Справка для университета",
        discuss="yes",
    )
    data.update(changes)
    return data


def send(client, data=None, file=None):
    fields = dict(data or new_form(), csrf=token(client))
    return client.post(
        "/patients/1/profile/extracts/requests/new",
        data=fields,
        files={"file": file} if file else None,
    )


def request_id(client):
    with connect(client.app.state.db_path) as c:
        return c.execute("SELECT max(id) FROM extract_requests").fetchone()[0]


def get(client, rid):
    with connect(client.app.state.db_path) as c:
        user = dict(
            c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        )
        return requests.get(c, client.app.state.db_path, user, 1, rid)


def reply(client, rid, **changes):
    data = dict(
        csrf=token(client),
        revision=get(client, rid)["revision"],
        client_token=secrets.token_urlsafe(24),
        status="clarify",
        reply="Уточните получателя",
    )
    data.update(changes)
    return client.post(f"/patients/1/profile/extracts/requests/{rid}/reply", data=data)


def test_prefill_from_current_private_identity_and_submitted_contact_without_sharing_other_draft_fields(
    client,
):
    login(client, "patient1")
    answer = lambda value: dict(status="answered", value=value)
    with connect(client.app.state.db_path) as c:
        owner = c.execute(
            "SELECT id FROM accounts WHERE username='patient1'"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(1,1,'test',?,?,'test')",
            (
                json.dumps(
                    dict(
                        answers=dict(
                            personal=dict(
                                full_name=answer("Прежнее имя"),
                                contact=answer("saved@example.test"),
                            )
                        )
                    ),
                    ensure_ascii=False,
                ),
                owner,
            ),
        )
        c.execute(
            "INSERT INTO intake_drafts VALUES(1,2,'test',?,'test')",
            (
                json.dumps(
                    dict(
                        personal=dict(
                            full_name=answer("Текущее имя"),
                            birth_date=answer("1994-06-11"),
                        ),
                        complaints=dict(comment=answer("PRIVATE_UNSENT_COMPLAINT")),
                    ),
                    ensure_ascii=False,
                ),
            ),
        )
    page = client.get("/patients/1/profile/extracts")
    assert page.status_code == 200
    assert (
        'value="Текущее имя"' in page.text
        and 'value="1994-06-11"' in page.text
        and 'value="saved@example.test"' in page.text
    )
    assert "PRIVATE_UNSENT_COMPLAINT" not in page.text
    assert send(client).status_code == 200
    login(client, "doctor")
    assert (
        "PRIVATE_UNSENT_COMPLAINT"
        not in client.get(
            f"/patients/1/profile/extracts/requests/{request_id(client)}"
        ).text
    )
    assert "Текущее имя" not in json.dumps(
        get(client, request_id(client)), ensure_ascii=False
    )


def test_all_six_kinds_and_request_resubmission_are_persistent_and_idempotent(client):
    login(client, "patient1")
    for kind in requests.KINDS:
        fields = new_form(kind=kind)
        page = send(client, fields)
        assert page.status_code == 200, page.text
        assert (
            requests.KINDS[kind] in page.text
            and "Запрос отправлен вашему специалисту" in page.text
        )
        rid = request_id(client)
        assert send(client, fields).status_code == 200
        assert request_id(client) == rid
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM extract_requests").fetchone()[0] == 6
        payload = c.execute("SELECT payload FROM extract_requests LIMIT 1").fetchone()[
            0
        ]
        assert "Пациент из запроса".encode() not in payload
    login(client, "doctor")
    inbox = client.get("/doctor/extract-requests")
    assert inbox.status_code == 200 and len(inbox.context["items"]) == 6
    assert client.get("/doctor").context["pending_extract_requests"] == 6


def test_deadline_validation_preserves_form_and_patient_cannot_choose_ready_status(
    client,
):
    login(client, "patient1")
    bad = send(
        client, new_form(due="date", due_date="", comment="Сохраните мой комментарий")
    )
    assert (
        bad.status_code == 422
        and "Сохраните мой комментарий" in bad.text
        and 'name="doc_0_due_date"' in bad.text
    )
    assert send(client, new_form(birth_date="2099-01-01")).status_code == 422
    assert (
        send(
            client,
            new_form(due="date", due_date=(today() - timedelta(days=1)).isoformat()),
        ).status_code
        == 422
    )
    assert (
        send(
            client,
            new_form(due="date", due_date=(today() + timedelta(days=1)).isoformat()),
        ).status_code
        == 200
    )
    rid = request_id(client)
    result = reply(client, rid, status="ready", reply="Уточнение пациента")
    assert result.status_code == 200
    assert get(client, rid)["status"] == "received"


def test_roles_csrf_foreign_requests_and_specialist_inbox_scope(client):
    login(client, "patient1")
    assert send(client).status_code == 200
    rid = request_id(client)
    assert client.get("/doctor/extract-requests").status_code == 403
    assert (
        client.post(
            f"/patients/1/profile/extracts/requests/{rid}/draft",
            data=dict(csrf=token(client), revision=1),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/patients/1/profile/extracts/requests/new",
            data=dict(new_form(), csrf="wrong"),
        ).status_code
        == 403
    )
    login(client, "patient2")
    assert client.get(f"/patients/1/profile/extracts/requests/{rid}").status_code == 403
    login(client, "doctor")
    assert send(client).status_code == 403
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой врач')")
        c.execute(
            "INSERT INTO accounts(username,password_hash,role,doctor_id) VALUES('doctor2',?,'doctor',2)",
            (TEST_HASH,),
        )
    client.post("/logout", data=dict(csrf=token(client)))
    assert (
        client.post(
            "/login",
            data=dict(
                csrf=token(client, "/login/specialist"),
                portal="specialist",
                username="doctor2",
                password=TEST_PASSWORD,
            ),
        ).status_code
        == 200
    )
    assert client.get("/doctor/extract-requests").context["items"] == []
    assert client.get(f"/patients/1/profile/extracts/requests/{rid}").status_code == 403


def test_reply_history_patient_clarification_and_stale_form_preserve_text(client):
    login(client, "patient1")
    send(client)
    rid = request_id(client)
    login(client, "doctor")
    assert reply(client, rid).status_code == 200
    assert get(client, rid)["status"] == "clarify"
    stale = reply(client, rid, revision=1, reply="Текст второго окна")
    assert stale.status_code == 409 and "Текст второго окна" in stale.text
    assert len(get(client, rid)["events"]) == 1
    login(client, "patient1")
    assert reply(client, rid, reply="Для учебного отдела").status_code == 200
    assert get(client, rid)["status"] == "received"
    assert len(get(client, rid)["events"]) == 2
    login(client, "doctor")
    assert reply(client, rid, status="ready", reply="").status_code == 422
    assert (
        reply(
            client, rid, status="ready", reply="Справку можно забрать на приёме"
        ).status_code
        == 200
    )


def test_patient_and_doctor_files_are_encrypted_downloadable_and_scoped(client):
    login(client, "patient1")
    body = b"patient attachment\n"
    fields = new_form()
    assert send(client, fields, ("example.txt", body, "text/plain")).status_code == 200
    rid = request_id(client)
    assert send(client, fields, ("example.txt", body, "text/plain")).status_code == 200
    with connect(client.app.state.db_path) as c:
        row = c.execute("SELECT * FROM extract_request_files").fetchone()
        fid = row["id"]
        assert row["content"] != body
        assert (
            c.execute("SELECT count(*) FROM extract_request_files").fetchone()[0] == 1
        )
    endpoint = f"/patients/1/profile/extracts/requests/{rid}/files/{fid}"
    assert client.get(endpoint).status_code == 200
    response = client.get(endpoint + "/content?download=true&ack_unscanned=true")
    assert response.status_code == 200 and response.content == body
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    login(client, "patient2")
    assert client.get(endpoint).status_code == 403
    login(client, "doctor")
    assert (
        client.get(endpoint + "/content?download=true&ack_unscanned=true").content
        == body
    )
    response = client.post(
        f"/patients/1/profile/extracts/requests/{rid}/reply",
        data=dict(
            csrf=token(client),
            revision=1,
            client_token=secrets.token_urlsafe(24),
            status="ready",
            reply="Готовый файл",
        ),
        files={"file": ("reply.txt", b"doctor response", "text/plain")},
    )
    assert response.status_code == 200
    login(client, "patient1")
    html = client.get(f"/patients/1/profile/extracts/requests/{rid}").text
    assert "reply.txt" in html and "Готовый файл" in html


def test_invalid_file_is_atomic_and_preserves_request_fields(client):
    login(client, "patient1")
    response = send(
        client,
        new_form(comment="Не терять текст"),
        ("wrong.exe", b"bad", "application/octet-stream"),
    )
    assert response.status_code == 422 and "Не терять текст" in response.text
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM extract_requests").fetchone()[0] == 0
        assert (
            c.execute("SELECT count(*) FROM extract_request_files").fetchone()[0] == 0
        )


def test_draft_prefill_publish_withdraw_and_duplicate_draft_do_not_publish_private_work(
    client,
):
    login(client, "patient1")
    send(
        client,
        new_form(
            kind="copy",
            full_name="Исправленное имя",
            purpose="other",
            purpose_other="Архив учреждения",
        ),
    )
    rid = request_id(client)
    login(client, "doctor")
    endpoint = f"/patients/1/profile/extracts/requests/{rid}/draft"
    assert (
        client.post(endpoint, data=dict(csrf=token(client), revision=1)).status_code
        == 200
    )
    item = get(client, rid)
    eid = item["document_id"]
    doc = current_document(client, eid)
    assert doc["data"]["identity"]["full_name"] == "Исправленное имя"
    assert doc["data"]["identity"]["birth_date"] == "2000-01-01"
    assert doc["data"]["title"] == "Копия медицинской документации"
    assert doc["data"]["recipient"] == "Архив учреждения"
    assert not doc["data"]["reviewed"] and not doc["published_revision"]
    assert (
        client.post(endpoint, data=dict(csrf=token(client), revision=1)).status_code
        == 200
    )
    assert get(client, rid)["document_id"] == eid
    login(client, "patient1")
    assert client.get(f"/patients/1/profile/extracts/{eid}").status_code == 404
    assert (
        "Продолжить оформление"
        not in client.get(f"/patients/1/profile/extracts/requests/{rid}").text
    )
    login(client, "doctor")
    form = form_for(doc, intent="publish", reviewed="yes", csrf=token(client))
    assert (
        client.post(f"/patients/1/profile/extracts/{eid}/save", data=form).status_code
        == 200
    )
    assert get(client, rid)["status"] == "ready"
    login(client, "patient1")
    page = client.get(f"/patients/1/profile/extracts/requests/{rid}")
    assert "Посмотреть готовый документ" in page.text
    assert client.get(f"/patients/1/profile/extracts/{eid}").status_code == 200
    login(client, "doctor")
    form = form_for(
        current_document(client, eid), intent="withdraw", csrf=token(client)
    )
    assert (
        client.post(f"/patients/1/profile/extracts/{eid}/save", data=form).status_code
        == 200
    )
    assert get(client, rid)["status"] == "in_progress"
    login(client, "patient1")
    assert (
        "Посмотреть готовый документ"
        not in client.get(f"/patients/1/profile/extracts/requests/{rid}").text
    )


def test_prescription_request_creates_information_response_not_prescription_or_medication_plan(
    client,
):
    login(client, "patient1")
    send(client, new_form(kind="prescription", purpose="renewal"))
    rid = request_id(client)
    login(client, "doctor")
    response = client.post(
        f"/patients/1/profile/extracts/requests/{rid}/draft",
        data=dict(csrf=token(client), revision=1),
    )
    assert response.status_code == 200
    doc = current_document(client, get(client, rid)["document_id"])
    assert doc["data"]["title"] == "Ответ на запрос рецепта"
    assert not doc["data"]["diagnosis"]
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM medicine_drafts").fetchone()[0] == 0
