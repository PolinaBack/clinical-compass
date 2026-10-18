import json
import re
import secrets
from datetime import date
from io import BytesIO

from app.storage import connect
from app.security import hash_password
from tests.helpers import login, token, TEST_PASSWORD, TEST_HASH


def rows(client, sql, args=()):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def start(client):
    r = client.post("/assessments/start", data={"csrf": token(client, "/me")})
    assert r.status_code == 200
    return int(r.url.path.split("/")[-1])


def save_data(client, aid, values, intent="submit", **extras):
    page = client.get(f"/assessments/{aid}")
    assert page.status_code == 200
    data = {
        "csrf": token(client, f"/assessments/{aid}"),
        "intent": intent,
        "request_id": re.search(
            r'name="request_id"\s+value="([^"]+)"', page.text
        ).group(1),
        "revision": re.search(r'name="revision"\s+value="([^"]+)"', page.text).group(1),
    }
    data.update({f"answer_{i}": value for i, value in enumerate(values, 1)})
    data.update(extras)
    return data


def save(client, aid, values, intent="submit", **extras):
    data = save_data(client, aid, values, intent, **extras)
    return client.post(f"/assessments/{aid}/save", data=data)


def assign(client, sid, scale):
    r = client.post(
        "/patients/1/assign",
        data={
            "csrf": token(client, "/patients/1"),
            "session_id": sid,
            "scale": scale,
            "due_day": date.today().isoformat(),
        },
    )
    assert r.status_code == 200
    return rows(
        client,
        "SELECT id FROM assessments WHERE session_id=? AND scale=?",
        (sid, scale),
    )[0]["id"]


def test_case_patient_specialist_patient_and_safety_lifecycle(client):
    login(client, "patient1")
    aid = start(client)
    assert (
        save(
            client, aid, [1, 2, 1, 2], comment_1="Синтетическая тема", discuss_1="true"
        ).status_code
        == 200
    )
    sid = rows(client, "SELECT session_id FROM assessments WHERE id=?", (aid,))[0][
        "session_id"
    ]
    login(client, "doctor")
    card = client.get("/patients/1").text
    assert "Предложение по PHQ-2" in card and "Предложение по GAD-2" in card
    q9 = assign(client, sid, "phq9")
    g7 = assign(client, sid, "gad7")
    # Reading and discussing are independent.
    client.post(
        f"/assessments/{aid}/review", data={"csrf": token(client), "intent": "read"}
    )
    assert (
        rows(
            client,
            "SELECT discussed FROM answers WHERE assessment_id=? AND item=?",
            (aid, "1"),
        )[0]["discussed"]
        == 0
    )
    client.post(
        f"/assessments/{aid}/review",
        data={"csrf": token(client), "intent": "discussed", "item": "1"},
    )
    assert (
        rows(
            client,
            "SELECT discussed FROM answers WHERE assessment_id=? AND item=?",
            (aid, "1"),
        )[0]["discussed"]
        == 1
    )
    login(client, "patient1")
    assert save(client, q9, [0] * 8 + [1], intent="draft").status_code == 200
    event = rows(client, "SELECT * FROM assessment_events")[0]
    assert event["kind"] == "phq9_item9" and event["acknowledged_at"] is None
    assert save(client, q9, [0] * 8 + [1]).status_code == 200
    assert save(client, g7, [0] * 7).status_code == 200
    assert len(rows(client, "SELECT * FROM assessment_events")) == 1
    assert (
        "Исследовательское" in client.get("/patients/1/results").text
        or "исследовательское" in client.get("/patients/1/results").text
    )
    login(client, "doctor")
    eid = event["id"]
    events = client.get("/api/workflow-alerts").json()["events"]
    assert events[0]["delivered_at"] is None
    for intent in ["delivered", "acknowledge", "action"]:
        response = client.post(
            f"/events/{eid}/status",
            data={
                "csrf": token(client),
                "intent": intent,
                "action_text": "Синтетическое документированное действие",
            },
        )
        assert response.status_code == 200
    event = rows(client, "SELECT * FROM assessment_events")[0]
    assert event["delivered_at"] and event["acknowledged_at"] and event["action_at"]
    assert "assessment_assigned" in client.get("/patients/1/audit").text


def test_login_cannot_select_or_switch_privilege(client):
    login(client, "patient1")
    assert "Сменить демороль" not in client.get("/me").text
    assert "уже в своём кабинете" in client.get("/login/specialist").text
    assert (
        client.post(
            "/login",
            data={
                "csrf": token(client),
                "username": "doctor",
                "password": TEST_PASSWORD,
                "portal": "specialist",
            },
        ).status_code
        == 409
    )
    client.post("/logout", data={"csrf": token(client)})
    assert (
        client.post(
            "/login",
            data={
                "csrf": token(client, "/login/specialist"),
                "username": "patient1",
                "password": TEST_PASSWORD,
                "portal": "specialist",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/login", data={"csrf": token(client), "role": "doctor"}
        ).status_code
        == 422
    )
    assert client.get("/doctor").status_code == 401


def test_specialist_cannot_access_another_specialist_patients(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Синтетический специалист 2')")
        c.execute(
            "INSERT INTO accounts(username,password_hash,role,doctor_id) VALUES('other',?,'doctor',2)",
            (TEST_HASH,),
        )
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
    assert client.get("/patients/2").status_code == 403
    assert "Демо-пациент Б" not in client.get("/doctor").text
    assert rows(
        client, "SELECT * FROM audit WHERE action='access_denied' AND patient_id=2"
    )


def test_consent_refusal_withdrawal_and_public_access(client):
    login(client, "patient1", consent=False)
    assert client.get("/faq").status_code == 200
    assert client.get("/help-now").status_code == 200
    assert (
        client.post("/assessments/start", data={"csrf": token(client)}).status_code
        == 403
    )
    assert (
        client.post(
            "/consent", data={"csrf": token(client), "accepted": "true"}
        ).status_code
        == 422
    )
    client.post(
        "/consent", data={"csrf": token(client), "accepted": "true", "adult": "true"}
    )
    aid = start(client)
    client.post("/consent", data={"csrf": token(client)})
    assert client.get("/faq").status_code == 200
    assert (
        client.post(
            f"/assessments/{aid}/save", data={"csrf": token(client)}
        ).status_code
        == 403
    )
    assert rows(client, "SELECT accepted FROM consents ORDER BY id") == [
        {"accepted": 1},
        {"accepted": 0},
    ]


def test_drafts_null_history_idempotence_and_conflicting_tabs(client):
    login(client, "patient1")
    aid = start(client)
    data = save_data(client, aid, [0, "declined", "deferred", ""], intent="draft")
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    assert len(rows(client, "SELECT * FROM answers")) == 4
    assert rows(client, "SELECT value,state FROM answers WHERE item='2'")[0] == {
        "value": None,
        "state": "declined",
    }
    stale = {**data, "request_id": secrets.token_urlsafe(24), "answer_1": 3}
    assert client.post(f"/assessments/{aid}/save", data=stale).status_code == 409
    assert save(client, aid, [1, 0, 0, 0], intent="draft").status_code == 200
    assert rows(client, "SELECT * FROM answer_history")
    assert "Черновик" in client.get(f"/assessments/{aid}").text


def test_assignment_and_raw_answers_role_boundary(client):
    login(client, "patient1")
    aid = start(client)
    sid = rows(client, "SELECT session_id FROM assessments")[0]["session_id"]
    assert (
        client.post(
            "/patients/1/assign",
            data={
                "csrf": token(client),
                "session_id": sid,
                "scale": "phq9",
                "due_day": date.today().isoformat(),
            },
        ).status_code
        == 403
    )
    login(client, "doctor")
    q9 = assign(client, sid, "phq9")
    assert (
        client.post(f"/assessments/{q9}/save", data={"csrf": token(client)}).status_code
        == 403
    )
    login(client, "patient2")
    assert client.get(f"/assessments/{aid}").status_code == 403
    assert client.get("/patients/1/results").status_code == 403
    assert client.get("/patients/1/audit").status_code == 403


def test_local_audio_keeps_original_separate_and_enforces_access(client):
    login(client, "patient1")
    aid = start(client)
    # Synthetic WebM container header; verifies storage/permissions, not a real microphone.
    payload = b"\x1aE\xdf\xa3" + b"synthetic-test-payload"
    r = client.post(
        f"/assessments/{aid}/audio",
        data={"csrf": token(client), "item": "1", "duration": "1", "consent": "true"},
        files={"recording": ("synthetic.webm", BytesIO(payload), "audio/webm")},
    )
    assert r.status_code == 200
    audio = rows(client, "SELECT id,transcript_status FROM audio_notes")[0]
    assert audio["transcript_status"] == "not_transcribed"
    assert client.get(f"/audio/{audio['id']}").content == payload
    assert not rows(client, "SELECT * FROM answers")
    login(client, "patient2")
    assert client.get(f"/audio/{audio['id']}").status_code == 403
    login(client, "doctor")
    assert client.get(f"/audio/{audio['id']}").content == payload
    login(client, "patient1")
    assert (
        client.post(
            f"/audio/{audio['id']}/delete", data={"csrf": token(client)}
        ).status_code
        == 200
    )
    assert client.get(f"/audio/{audio['id']}").status_code == 404


def test_audio_requires_separate_consent_and_valid_container(client):
    login(client, "patient1")
    aid = start(client)
    data = {"csrf": token(client), "item": "1", "duration": "1"}
    files = {"recording": ("synthetic.webm", BytesIO(b"not-audio"), "audio/webm")}
    assert (
        client.post(f"/assessments/{aid}/audio", data=data, files=files).status_code
        == 422
    )
    data["consent"] = "true"
    assert (
        client.post(f"/assessments/{aid}/audio", data=data, files=files).status_code
        == 422
    )


def test_repeat_is_specialist_assignment_and_keeps_old_session(client):
    login(client, "patient1")
    aid = start(client)
    save(client, aid, [0, 0, 0, 0])
    assert (
        client.post("/assessments/start", data={"csrf": token(client)}).status_code
        == 409
    )
    assert (
        client.post("/patients/1/repeat", data={"csrf": token(client)}).status_code
        == 403
    )
    login(client, "doctor")
    assert (
        client.post("/patients/1/repeat", data={"csrf": token(client)}).status_code
        == 200
    )
    sessions = rows(client, "SELECT id FROM assessment_sessions")
    assert len(sessions) == 2
    # Double-clicking repeat does not create another open PHQ-4.
    client.post("/patients/1/repeat", data={"csrf": token(client)})
    assert len(rows(client, "SELECT id FROM assessment_sessions")) == 2


def test_new_positive_item9_after_resolved_episode_creates_new_event(client):
    login(client, "patient1")
    aid = start(client)
    sid = rows(client, "SELECT session_id FROM assessments")[0]["session_id"]
    login(client, "doctor")
    q9 = assign(client, sid, "phq9")
    login(client, "patient1")
    save(client, q9, [0] * 8 + [1])
    login(client, "doctor")
    eid = rows(client, "SELECT id FROM assessment_events")[0]["id"]
    for intent in ["acknowledge", "action"]:
        client.post(
            f"/events/{eid}/status",
            data={
                "csrf": token(client),
                "intent": intent,
                "action_text": "Синтетическое действие",
            },
        )
    login(client, "patient1")
    save(client, q9, [0] * 9)
    save(client, q9, [0] * 8 + [1])
    events = rows(client, "SELECT * FROM assessment_events ORDER BY id")
    assert len(events) == 2 and events[1]["acknowledged_at"] is None


def test_hcl33_context_excluded_and_no_fabricated_threshold(client):
    login(client, "patient1")
    aid = start(client)
    sid = rows(client, "SELECT session_id FROM assessments")[0]["session_id"]
    login(client, "doctor")
    hcl = assign(client, sid, "hcl33")
    login(client, "patient1")
    response = save(
        client, hcl, [1] * 33, context_today="0", context_year="1", context_days="200"
    )
    assert response.status_code == 200
    assert rows(client, "SELECT value,state FROM answers WHERE item='ctx_days'")[0] == {
        "value": None,
        "state": "not_asked_by_rule",
    }
    from app.scales import score

    raw = {
        r["item"]: r
        for r in rows(client, "SELECT * FROM answers WHERE assessment_id=?", (hcl,))
    }
    assert score("hcl33", raw)["total"] == 33
    login(client, "doctor")
    page = client.get(f"/assessments/{hcl}").text
    assert "33/33" in page and "порог бар не применяется" in page.lower()


def test_password_reset_revokes_existing_session(client):
    login(client, "patient1")
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE accounts SET auth_version=auth_version+1 WHERE username='patient1'"
        )
    assert client.get("/me").status_code == 401


def test_public_materials_available_without_consent_or_account(client):
    for slug in ["faq", "additional", "brief"]:
        response = client.get("/project-sources/" + slug)
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert client.get("/project-sources/unknown").status_code == 404
    for url in ["/faq", "/privacy", "/help-now"]:
        assert client.get(url).status_code == 200


def test_invalid_answer_rolls_back_entire_submission_and_history_is_scoped(client):
    login(client, "patient1")
    aid = start(client)
    assert save(client, aid, [0, 0, 9, 0]).status_code == 422
    assert rows(client, "SELECT * FROM answers") == []
    save(client, aid, [0, 0, 0, 0])
    save(client, aid, [1, 0, 0, 0])
    assert "Предыдущие версии ответов" in client.get(f"/assessments/{aid}/history").text
    login(client, "patient2")
    assert client.get(f"/assessments/{aid}/history").status_code == 403
