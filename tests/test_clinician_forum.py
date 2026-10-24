"""Real storage/authorization/publication checks on disposable synthetic data."""

import re
from html import unescape

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import connect
from tests.helpers import TEST_HASH, TEST_PASSWORD, login, token


def payload(client, **changes):
    data = dict(
        csrf=token(client, "/doctor/forum"),
        operation="topic",
        target_id="0",
        revision="0",
        kind="case",
        title="Трудности с засыпанием",
        body="Женщина 30–40 лет. Несколько недель трудно засыпать перед рабочими днями. Как уточнить связь со стрессом?",
        deidentified="true",
    )
    return data | changes


def preview(client, data):
    response = client.post("/doctor/forum/preview", data=data)
    assert response.status_code == 200, response.text
    nonce = re.search(r'name="preview_token" value="([^"]+)"', response.text).group(1)
    return dict(data, preview_token=unescape(nonce))


def publish(client, **changes):
    data = preview(client, payload(client, **changes))
    response = client.post("/doctor/forum/publish", data=data)
    assert response.status_code == 200, response.text
    return int(response.url.path.split("/")[-1]), data


def second_doctor(client):
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT OR IGNORE INTO doctors(id,name) VALUES(2,'Второй врач')")
        c.execute(
            "INSERT OR IGNORE INTO accounts(username,password_hash,role,doctor_id) VALUES('colleague',?,'doctor',2)",
            (TEST_HASH,),
        )
    client.post("/logout", data={"csrf": token(client)})
    assert (
        client.post(
            "/login",
            data=dict(
                csrf=token(client, "/login/specialist"),
                username="colleague",
                password=TEST_PASSWORD,
                portal="specialist",
            ),
        ).status_code
        == 200
    )


def rows(client, table):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM " + table)]


def test_thread_preview_storage_restart_comments_and_private_faq_separation(client):
    login(client, "doctor")
    original = payload(client)
    ready = preview(client, original)
    assert not rows(client, "clinician_topics")
    assert original["body"] not in client.cookies["session"]
    response = client.post("/doctor/forum/publish", data=ready)
    tid = int(response.url.path.split("/")[-1])
    assert response.status_code == 200
    assert client.post("/doctor/forum/publish", data=ready).status_code == 200
    assert len(rows(client, "clinician_topics")) == 1
    assert original["body"] not in client.get("/faq").text
    assert not rows(client, "forum_questions")
    second_doctor(client)
    assert original["body"] in client.get(f"/doctor/forum/topics/{tid}").text
    assert client.get("/patients/1/diary").status_code == 403
    publish(
        client,
        operation="comment",
        target_id=str(tid),
        revision="1",
        title="",
        body="Стоит уточнить режим сна и что помогает расслабиться вечером.",
    )
    assert len(rows(client, "clinician_comments")) == 1
    assert not rows(client, "messages")
    assert not rows(client, "patient_ai_runs")
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert (
            "что помогает расслабиться"
            in restarted.get(f"/doctor/forum/topics/{tid}").text
        )
        assert len(rows(restarted, "clinician_topics")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Почта: sample@example.org",
        "Связаться +7 (999) 123-45-67",
        "телефон 89991234567",
        "Дата рождения 03.04.1990",
        "Встреча 2026-10-24",
        "Родилась 3 апреля 1990",
        "Иванова Анна Сергеевна",
        "Иванова А. С.",
        "А. С. Иванова",
        "Пациентка Анна жалуется на сон",
        "Анна Сергеевна",
        "Анна Иванова",
        "Соколова Мария",
        "Её зовут Мария",
        "Адрес: Ленина",
        "ул. Ленина, дом 2",
        "паспорт: 1234 567890",
        "полис 1234567",
        "Ссылка /patients/1/diary",
        "patient_id=1",
        "https://example.org/card",
        "Контакт @sample_user",
        "sample\u200b@example.org",
    ],
)
def test_direct_identifiers_blocked_in_topic_comment_and_publish(client, text):
    login(client, "doctor")
    response = client.post("/doctor/forum/preview", data=payload(client, body=text))
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert not rows(client, "clinician_topics")
    tid, _ = publish(client)
    data = payload(
        client,
        operation="comment",
        target_id=str(tid),
        revision="1",
        title="",
        body=text,
    )
    assert client.post("/doctor/forum/preview", data=data).status_code == 422
    assert not rows(client, "clinician_comments")


def test_preview_bound_to_exact_text_account_and_confirmation_not_patient_links(client):
    login(client, "doctor")
    data = payload(client)
    assert (
        client.post(
            "/doctor/forum/preview", data=data | {"deidentified": "false"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/doctor/forum/preview", data=data | {"patient_id": "1"}
        ).status_code
        == 422
    )
    assert (
        client.post("/doctor/forum/preview", data=data | {"target_id": "1"}).status_code
        == 422
    )
    assert (
        client.post("/doctor/forum/preview", data=data | {"csrf": "bad"}).status_code
        == 403
    )
    assert (
        client.post(
            "/doctor/forum/publish", data=data | {"preview_token": "fake"}
        ).status_code
        == 409
    )
    ready = preview(client, data)
    assert (
        client.post(
            "/doctor/forum/publish", data=ready | {"body": "Другой обезличенный текст"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/doctor/forum/publish",
            data=ready | {"body": "Позвоните +7 (999) 123-45-67"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/doctor/forum/publish", data=ready | {"patient_id": "1"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/doctor/forum/preview", data=data, files={"file": ("case.txt", b"private")}
        ).status_code
        == 422
    )
    assert not rows(client, "clinician_topics")
    second_doctor(client)
    ready["csrf"] = token(client, "/doctor/forum")
    assert client.post("/doctor/forum/publish", data=ready).status_code == 409


def test_patients_anonymous_inactive_and_forged_sessions_cannot_access_forum(client):
    assert client.get("/doctor/forum").status_code == 401
    login(client, "doctor")
    tid, ready = publish(client)
    login(client, "patient1")
    assert 'href="/doctor/forum"' not in client.get("/me").text
    for path in (
        "/doctor/forum",
        "/doctor/forum/new",
        f"/doctor/forum/topics/{tid}",
        f"/doctor/forum/topics/{tid}/edit",
        "/doctor/forum/comments/1/edit",
    ):
        assert client.get(path).status_code == 403
    ready["csrf"] = token(client)
    for path in ("/doctor/forum/preview", "/doctor/forum/publish"):
        assert client.post(path, data=ready).status_code == 403
    for intent in ("delete", "report"):
        assert (
            client.post(
                f"/doctor/forum/topics/{tid}/{intent}",
                data=dict(csrf=token(client), confirmed="true"),
            ).status_code
            == 403
        )
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("UPDATE accounts SET active=0 WHERE username='doctor'")
    assert client.get("/doctor/forum").status_code == 401
    assert client.post("/doctor/forum/publish", data=ready).status_code == 401


def test_own_edit_delete_foreign_rejection_stale_revision_and_escaping(client):
    login(client, "doctor")
    body = 'Синтетический случай <script>alert("x")</script> & контроль.'
    tid, _ = publish(client, body=body)
    page = client.get(f"/doctor/forum/topics/{tid}")
    assert "&lt;script&gt;" in page.text and '<script>alert("x")' not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "noindex" in page.headers["x-robots-tag"]
    stale = preview(
        client,
        payload(
            client,
            operation="edit_topic",
            target_id=str(tid),
            revision="1",
            body="Новый обезличенный текст",
        ),
    )
    publish(
        client,
        operation="edit_topic",
        target_id=str(tid),
        revision="1",
        kind="question",
        body="Исправленное описание без личных сведений",
    )
    assert client.post("/doctor/forum/publish", data=stale).status_code == 409
    assert rows(client, "clinician_topics")[0]["revision"] == 2
    second_doctor(client)
    assert client.get(f"/doctor/forum/topics/{tid}/edit").status_code == 403
    assert (
        client.post(
            f"/doctor/forum/topics/{tid}/delete",
            data=dict(csrf=token(client), confirmed="true"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/doctor/forum/preview",
            data=payload(
                client, operation="edit_topic", target_id=str(tid), revision="2"
            ),
        ).status_code
        == 403
    )
    publish(
        client,
        operation="comment",
        target_id=str(tid),
        revision="2",
        title="",
        body="Можно уточнить важные обстоятельства.",
    )
    cid = rows(client, "clinician_comments")[0]["id"]
    publish(
        client,
        operation="edit_comment",
        target_id=str(cid),
        revision="1",
        title="",
        body="Исправленный ответ коллеги.",
    )
    assert (
        client.post(
            f"/doctor/forum/comments/{cid}/delete",
            data=dict(csrf=token(client), confirmed="true"),
        ).status_code
        == 200
    )
    assert rows(client, "clinician_comments")[0]["body"] == ""
    login(client, "doctor")
    assert (
        client.post(
            f"/doctor/forum/topics/{tid}/delete",
            data=dict(csrf=token(client), confirmed="true"),
        ).status_code
        == 200
    )
    assert client.get(f"/doctor/forum/topics/{tid}").status_code == 404
    assert rows(client, "clinician_topics")[0]["body"] == ""
    assert not rows(client, "diary")
    # Audit contains only action/IDs/times, never publication contents.
    assert all(body not in str(r) for r in rows(client, "audit"))


def test_privacy_report_immediately_hides_topic_or_comment_until_author_corrects(
    client,
):
    login(client, "doctor")
    tid, _ = publish(client)
    publish(
        client,
        operation="comment",
        target_id=str(tid),
        revision="1",
        title="",
        body="Первый обезличенный ответ.",
    )
    cid = rows(client, "clinician_comments")[0]["id"]
    second_doctor(client)
    assert (
        client.post(
            f"/doctor/forum/comments/{cid}/report",
            data=dict(csrf=token(client), confirmed="true"),
        ).status_code
        == 200
    )
    assert (
        "Первый обезличенный ответ"
        not in client.get(f"/doctor/forum/topics/{tid}").text
    )
    assert (
        client.post(
            f"/doctor/forum/topics/{tid}/report",
            data=dict(csrf=token(client), confirmed="true"),
        ).status_code
        == 200
    )
    assert client.get(f"/doctor/forum/topics/{tid}").status_code == 404
    assert "Трудности с засыпанием" not in client.get("/doctor/forum").text
    assert len(rows(client, "clinician_privacy_reports")) == 2
    login(client, "doctor")
    assert "Трудности с засыпанием" in client.get("/doctor/forum?mine=true").text
    publish(
        client,
        operation="edit_topic",
        target_id=str(tid),
        revision="2",
        body="Исправленный обезличенный случай.",
    )
    publish(
        client,
        operation="edit_comment",
        target_id=str(cid),
        revision="2",
        title="",
        body="Исправленный обезличенный ответ.",
    )
    second_doctor(client)
    assert (
        "Исправленный обезличенный ответ"
        in client.get(f"/doctor/forum/topics/{tid}").text
    )


def test_filter_pagination_task_entry_and_public_forum_unchanged(client):
    login(client, "doctor")
    for i in range(22):
        publish(
            client,
            title=f"Сон и усталость, тема {i}",
            kind="question" if i % 2 else "case",
        )
    page = client.get("/doctor/forum")
    assert page.text.count('class="card forum-topic"') == 20
    assert "Следующие темы" in page.text
    assert client.get("/doctor/forum?p=2").text.count('class="card forum-topic"') == 2
    assert (
        client.get("/doctor/forum?kind=case").text.count('class="card forum-topic"')
        == 11
    )
    assert "Ничего не найдено" in client.get("/doctor/forum?q=%25").text
    assert client.get("/doctor/forum?q=СОН").text.count('class="card forum-topic"') == 20
    tasks = client.get("/doctor/tasks").text
    assert tasks.index('id="clinician-forum"') < tasks.index('id="faq-forum"')
    assert 'href="/doctor/forum"' not in client.get("/doctor/calendar").text
    assert not rows(client, "forum_questions")
