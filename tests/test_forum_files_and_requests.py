"""Forum publication boundaries, explicit questionnaire requests and derived age."""

import json
import re
import secrets
from html import unescape
from fastapi.testclient import TestClient

from app.intake_catalog import VERSION
from app.services import intake
from app.services.clinical_forms import today
from app.storage import connect
from tests.helpers import login, token
from tests.test_clinician_forum import payload, second_doctor

CONTENT = "Обезличенный учебный пример без контактов и личных сведений.".encode()


def ready_file(client, **changes):
    data = payload(client, **changes)
    response = client.post(
        "/doctor/forum/preview",
        data=data,
        files={"file": ("example.txt", CONTENT, "text/plain")},
    )
    assert response.status_code == 200, response.text
    data["preview_token"] = unescape(
        re.search(r'name="preview_token" value="([^"]+)"', response.text).group(1)
    )
    data["attachments"] = unescape(
        re.search(r'name="attachments" value="([^"]+)"', response.text).group(1)
    )
    return data, json.loads(data["attachments"])[0]


def test_topic_and_answer_files_preview_publication_and_scope(client):
    login(client, "doctor")
    data, fid = ready_file(client)
    assert client.get(f"/forum/files/{fid}").content == CONTENT
    peer = TestClient(client.app)
    second_doctor(peer)
    assert peer.get(f"/forum/files/{fid}").status_code == 403
    result = client.post("/doctor/forum/publish", data=data)
    assert result.status_code == 200
    tid = int(result.url.path.rsplit("/", 1)[-1])
    assert "example.txt" in result.text
    assert client.post("/doctor/forum/publish", data=data).status_code == 200
    reply, rid = ready_file(
        client,
        operation="comment",
        target_id=str(tid),
        revision="1",
        title="",
        body="Предлагаю уточнить обстоятельства и режим сна без персональных подробностей.",
    )
    assert client.post("/doctor/forum/publish", data=reply).status_code == 200
    for ident in (fid, rid):
        response = peer.get(f"/forum/files/{ident}")
        assert response.status_code == 200 and response.content == CONTENT
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
    with connect(client.app.state.db_path) as c:
        rows = c.execute("SELECT * FROM forum_attachments").fetchall()
        assert len(rows) == 2 and all(CONTENT not in r["content"] for r in rows)
        assert rows[0]["topic_id"] == tid and rows[1]["comment_id"] is not None
        c.execute("UPDATE clinician_topics SET status='hidden' WHERE id=?", (tid,))
    assert peer.get(f"/forum/files/{fid}").status_code == 404
    peer.close()
    login(client, "patient1")
    assert client.get(f"/forum/files/{fid}").status_code == 403


def test_attachment_list_is_bound_to_preview_and_cannot_borrow_a_file(client):
    login(client, "doctor")
    first, fid = ready_file(client)
    second, other = ready_file(client)
    assert (
        client.post(
            "/doctor/forum/publish", data=first | {"attachments": json.dumps([other])}
        ).status_code
        == 409
    )
    second_doctor(client)
    response = client.post(
        "/doctor/forum/preview",
        data=payload(client) | {"attachments": json.dumps([fid])},
    )
    assert response.status_code == 403
    with connect(client.app.state.db_path) as c:
        assert c.execute("select count(*) from clinician_topics").fetchone()[0] == 0


def test_public_faq_files_follow_moderation_for_question_and_reply(client):
    form = dict(
        csrf=token(client, "/faq"),
        form_id=secrets.token_urlsafe(24),
        body="Как подготовиться к первой консультации?",
        public_consent="true",
    )
    response = client.post(
        "/faq/questions",
        data=form,
        files={"file": ("question.txt", CONTENT, "text/plain")},
    )
    assert response.status_code == 200
    question_url = response.url.path
    with connect(client.app.state.db_path) as c:
        qid = c.execute("select id from forum_questions").fetchone()[0]
        row = c.execute("select * from forum_attachments").fetchone()
        fid = row["id"]
        assert row["owner_id"] is None
    assert client.get(f"/forum/files/{fid}").status_code == 404
    assert (
        client.post(
            "/faq/questions",
            data=form,
            files={"file": ("question.txt", CONTENT, "text/plain")},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/faq/questions",
            data=form,
            files={"file": ("different.txt", b"other", "text/plain")},
        ).status_code
        == 409
    )
    login(client, "doctor")
    assert client.get(f"/forum/files/{fid}").content == CONTENT
    answer = dict(
        csrf=token(client, "/doctor/faq"),
        intent="publish",
        reply="Подготовьте общие вопросы и сведения, которые готовы обсудить.",
    )
    assert (
        client.post(
            f"/doctor/faq/{qid}",
            data=answer,
            files={"file": ("answer.txt", CONTENT, "text/plain")},
        ).status_code
        == 200
    )
    client.post("/logout", data=dict(csrf=token(client)))
    page = client.get(question_url)
    assert "question.txt" in page.text and "answer.txt" in page.text
    with connect(client.app.state.db_path) as c:
        ids = [r[0] for r in c.execute("select id from forum_attachments")]
    for ident in ids:
        assert client.get(f"/forum/files/{ident}").content == CONTENT
    login(client, "doctor")
    assert (
        client.post(
            f"/doctor/faq/{qid}", data=dict(csrf=token(client), intent="hide", reply="")
        ).status_code
        == 200
    )
    client.post("/logout", data=dict(csrf=token(client)))
    for ident in ids:
        assert client.get(f"/forum/files/{ident}").status_code == 404


def test_file_validation_does_not_publish_partial_topic(client):
    login(client, "doctor")
    for name, content in [
        ("bad.exe", b"executable"),
        ("fake.pdf", b"not PDF"),
        ("bad.txt", b"\x00binary"),
    ]:
        response = client.post(
            "/doctor/forum/preview",
            data=payload(client),
            files={"file": (name, content)},
        )
        assert response.status_code == 422
    with connect(client.app.state.db_path) as c:
        assert c.execute("select count(*) from clinician_topics").fetchone()[0] == 0
        assert c.execute("select count(*) from forum_attachments").fetchone()[0] == 0


def test_preferences_only_appear_after_doctor_request_and_require_explicit_send(client):
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert "data-med-preferences" not in page.text
    answer = dict(
        csrf=token(client),
        revision="0",
        intent="send",
        effect="S014",
        primary="S014",
        comment="",
    )
    assert (
        client.post("/patients/1/assignments/preferences", data=answer).status_code
        == 403
    )
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert "Назначить анкету пожеланий по побочным эффектам" in page.text
    nonce = secrets.token_urlsafe(24)
    send = dict(csrf=token(client), client_token=nonce)
    assert (
        client.post(
            "/patients/1/assignments/preferences/request", data=send
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/patients/1/assignments/preferences/request", data=send
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("select count(*) from medication_preference_requests").fetchone()[
                0
            ]
            == 1
        )
    login(client, "patient1")
    assert "data-med-preferences" in client.get("/patients/1/profile/assignments").text
    answer["csrf"] = token(client)
    assert (
        client.post(
            "/patients/1/assignments/preferences", data=answer | {"intent": "draft"}
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "select answered_at from medication_preference_requests"
            ).fetchone()[0]
            is None
        )
    answer["revision"] = "1"
    assert (
        client.post("/patients/1/assignments/preferences", data=answer).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "select answer_revision from medication_preference_requests"
            ).fetchone()[0]
            == 2
        )
    assert (
        client.post(
            "/patients/1/assignments/preferences/request",
            data=dict(csrf=token(client), client_token=secrets.token_urlsafe(24)),
        ).status_code
        == 403
    )


def test_birth_date_has_derived_age_and_removed_panels_are_absent(client):
    login(client, "patient1")
    day = today()
    birth = f"{day.year-30}-01-01"
    response = client.post(
        "/patients/1/intake/sections/personal",
        data=dict(
            csrf=token(client),
            revision="0",
            catalog_version=VERSION,
            a_birth_date=birth,
            s_birth_date="answered",
        ),
    )
    assert response.status_code == 200
    page = client.get("/patients/1/profile/about/personal")
    assert "Дата рождения" in page.text and "data-intake-age" in page.text
    assert "Возраст: <strong>30</strong>" in page.text
    assert (
        'name="a_age"' not in page.text
        and "<h2>Связанные сведения</h2>" not in page.text
    )
    page = client.get("/patients/1/profile/about/medicines")
    assert (
        'name="a_avoid_reactions"' not in page.text
        and 'name="a_primary_reaction"' not in page.text
    )
    assert "<h2>Связанные сведения</h2>" not in page.text
