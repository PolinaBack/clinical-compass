"""Manually entered cards reach local inference; no demo substitution or ACL bypass."""

import json
import sqlite3
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException

from app.storage import connect, now
from app.services import patient_ai as ai
from app.services.patient_ai_data import collect
from app.services.clinical_forms import today
from app.services.clinical_attachments import key_path
from app.experimental_ai.ollama import Ollama
from app.intake_catalog import VERSION
from tests.helpers import login, token
from tests.test_experimental_ai import response
from scripts.backup import backup


def seed(client, pid=1, note="MANUAL-CARD-ALPHA: тревога перед собеседованием"):
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO patient_profiles(patient_id,biography,goals,course,health,stressors,family_context,updated_at) VALUES(?,?,'','','','','',?) ON CONFLICT(patient_id) DO UPDATE SET biography=excluded.biography,updated_at=excluded.updated_at",
            (pid, note, now()),
        )
        for i in (0, 2, 5):
            c.execute(
                "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact,sleep_quality) VALUES(?,?,?,?,?,?,0,?)",
                (
                    pid,
                    (today() - timedelta(days=i)).isoformat(),
                    0 if i == 5 else 6,
                    5 + i / 10,
                    "Не указано",
                    note,
                    5,
                ),
            )


def enqueue(client, pid=1):
    csrf = token(client)
    r = client.post(
        f"/patients/{pid}/ai-analysis/run", data={"csrf": csrf}, follow_redirects=False
    )
    assert r.status_code == 303, r.text
    with connect(client.app.state.db_path) as c:
        return c.execute(
            "SELECT id FROM patient_ai_runs WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()[0]


def latest(client, pid=1):
    with connect(client.app.state.db_path) as c:
        row = c.execute(
            "SELECT * FROM patient_ai_runs WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        return ai.decoded(c, client.app.state.db_path, row)


def provider(calls, handler=None):
    def serve(req):
        calls.append(req)
        if req.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        if handler:
            return handler(req)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"content": json.dumps(response(), ensure_ascii=False)},
            },
        )

    return Ollama(httpx.MockTransport(serve))


def test_actual_manual_card_payload_and_independent_patients(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("COMPASS_EXPERIMENT_SOURCES", str(tmp_path / "sources"))
    login(client, "doctor")
    seed(client)
    with connect(client.app.state.db_path) as c:
        actor = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[
            0
        ]
        did = c.execute("SELECT id FROM diary WHERE patient_id=1 LIMIT 1").fetchone()[0]
        c.execute(
            "INSERT INTO diary_comments(patient_id,diary_id,author_id,body,created_at) VALUES(1,?,?,?,?)",
            (did, actor, "DOCTOR_MANUAL_COMMENT", now()),
        )
    seed(client, 2, "MANUAL-CARD-BETA: трудности с засыпанием")
    first = enqueue(client, 1)
    assert enqueue(client, 1) == first
    second = enqueue(client, 2)
    assert first != second
    calls = []
    assert ai.process_one(client.app.state.db_path, provider(calls))
    payload = json.loads(calls[-1].content)
    assert payload["options"]["num_ctx"] == 8192
    assert "MANUAL-CARD-ALPHA" in payload["messages"][1]["content"]
    assert "DOCTOR_MANUAL_COMMENT" in payload["messages"][1]["content"]
    assert "MANUAL-CARD-BETA" not in payload["messages"][1]["content"]
    assert "Мария Соколова" not in payload["messages"][1]["content"]
    assert "полностью вымышлены" not in payload["messages"][0]["content"]
    first_result = latest(client)
    assert first_result["status"] == "completed"
    assert first_result["provenance"]["input_mode"] == "patient-card"
    assert first_result["snapshot"]["metrics"]["wellbeing"]["n"] == 3
    assert first_result["snapshot"]["metrics"]["wellbeing"]["missing"] == 27
    assert first_result["snapshot"]["metrics"]["anxiety"]["n"] == 0
    assert first_result["snapshot"]["metrics"]["wellbeing"]["mean"] == 4
    assert ai.process_one(client.app.state.db_path, provider(calls))
    assert "MANUAL-CARD-BETA" in json.loads(calls[-1].content)["messages"][1]["content"]
    assert (
        "MANUAL-CARD-ALPHA"
        not in json.loads(calls[-1].content)["messages"][1]["content"]
    )
    html = client.get("/patients/1/ai-analysis").text
    assert "Краткая сводка" in html and "ТРАНСПОРТНАЯ ФИКСТУРА" in html
    assert "MANUAL-CARD-BETA" not in html
    assert client.get(f"/patients/1/ai-analysis?run_id={second}").status_code == 404


def test_new_manual_edit_generates_new_snapshot_no_cached_demo(client):
    login(client, "doctor")
    seed(client)
    enqueue(client)
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE patient_profiles SET biography='UPDATED-MANUAL-CONTENT' WHERE patient_id=1"
        )
    calls = []
    assert ai.process_one(client.app.state.db_path, provider(calls))
    assert not calls and latest(client)["error_code"] == "card_changed"
    enqueue(client)
    assert ai.process_one(client.app.state.db_path, provider(calls))
    assert (
        "UPDATED-MANUAL-CONTENT"
        in json.loads(calls[-1].content)["messages"][1]["content"]
    )
    assert latest(client)["status"] == "completed"


@pytest.mark.parametrize("change", ["card", "doctor", "participation", "account"])
def test_changes_during_roundtrip_preserve_received_answer_but_respect_access(
    client, change
):
    login(client, "doctor")
    seed(client)
    enqueue(client)

    def handler(req):
        with connect(client.app.state.db_path) as c:
            if change == "card":
                c.execute(
                    "UPDATE patient_profiles SET biography='changed during inference' WHERE patient_id=1"
                )
            elif change == "doctor":
                c.execute("INSERT INTO doctors(id,name) VALUES(2,'Другой врач')")
                c.execute("UPDATE patients SET doctor_id=2 WHERE id=1")
            elif change == "participation":
                c.execute(
                    "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(1,'withdraw',0,1,?)",
                    (now(),),
                )
            else:
                c.execute("UPDATE accounts SET active=0 WHERE role='doctor'")
        return httpx.Response(
            200, json={"done": True, "message": {"content": json.dumps(response())}}
        )

    ai.process_one(client.app.state.db_path, provider([], handler))
    if change == "card":
        assert latest(client)["status"] == "completed"
        assert latest(client)["result"] is not None
        assert latest(client)["provenance"]["input_changed_during_generation"] is True
        page = client.get("/patients/1/ai-analysis")
        assert 'id="patient-ai-result"' in page.text
        assert "Карта изменилась" in page.text
        return
    assert latest(client)["result"] is None
    assert latest(client)["error_code"] == "permission_changed"


def test_private_intake_draft_not_read_published_manual_answer_is_read(client):
    login(client, "doctor")
    seed(client)
    with connect(client.app.state.db_path) as c:
        actor = c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        c.execute(
            "INSERT INTO intake_drafts(patient_id,revision,catalog_version,answers_json,updated_at) VALUES(1,1,?,?,?)",
            (
                VERSION,
                json.dumps(
                    {
                        "complaints": {
                            "comment": {"status": "answered", "value": "PRIVATE-UNSENT"}
                        }
                    }
                ),
                now(),
            ),
        )
        data = collect(c, actor["id"], 1)
        assert "PRIVATE-UNSENT" not in json.dumps(data)
        snapshot = dict(
            answers={
                "personal": {
                    "full_name": {"status": "answered", "value": "PRIVATE FULL NAME"},
                    "birth_date": {"status": "answered", "value": "2000-01-01"},
                    "sex": {"status": "answered", "value": "Женский"},
                },
                "complaints": {
                    "comment": {
                        "status": "answered",
                        "value": "PUBLISHED-MANUAL-ANSWER",
                    }
                },
            },
            entities=[],
            preferences={},
        )
        c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(1,1,?,?,?,?)",
            (VERSION, json.dumps(snapshot), actor["id"], now()),
        )
        data = collect(c, actor["id"], 1)
        assert "PUBLISHED-MANUAL-ANSWER" in json.dumps(data)
        assert data["facts"][0]["values"]["Пол, по данным анкеты"] == "Женский"
        assert data["facts"][0]["values"]["Возраст, полных лет"] == today().year - 2000
        assert "PRIVATE FULL NAME" not in json.dumps(data)
        assert "PRIVATE-UNSENT" not in json.dumps(data)


@pytest.mark.parametrize(
    "failure,code",
    [
        ("connect", "provider_unavailable"),
        ("timeout", "timeout"),
        ("invalid", "invalid_response"),
        ("unsafe", "unsafe_response"),
    ],
)
def test_provider_errors_never_substitute_prepared_summary(client, failure, code):
    login(client, "doctor")
    seed(client)
    enqueue(client)

    def fail(req):
        if failure == "connect":
            raise httpx.ConnectError("PRIVATE ERROR", request=req)
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE ERROR", request=req)
        if failure == "invalid":
            return httpx.Response(
                200, json={"done": True, "message": {"content": "PRIVATE ERROR"}}
            )
        raw = response()
        raw["summary"] = (
            "Назначить сертралин 50 мг ежедневно для лечения. Остальное уточнить по карте."
        )
        return httpx.Response(
            200, json={"done": True, "message": {"content": json.dumps(raw)}}
        )

    ai.process_one(client.app.state.db_path, provider([], fail))
    job = latest(client)
    if failure in ("invalid", "unsafe"):
        assert job["status"] == "completed" and not job["error_code"]
        assert job["result"]["output_format"] == "free_text"
        assert job["provenance"]["output_validation"] == "not_checked"
        assert 'id="patient-ai-result"' in client.get("/patients/1/ai-analysis").text
        if failure == "invalid":
            assert job["result"]["text"] == "PRIVATE ERROR"
        else:
            assert "Назначить сертралин" in json.loads(job["result"]["text"])["summary"]
        return
    assert (
        job["status"] == "error" and job["result"] is None and job["error_code"] == code
    )
    assert "PRIVATE ERROR" not in json.dumps(job)
    assert 'id="patient-ai-result"' not in client.get("/patients/1/ai-analysis").text


def test_all_routes_doctor_only_foreign_scope_csrf_and_empty_card(client):
    login(client, "doctor")
    csrf = token(client)
    assert (
        client.post("/patients/1/ai-analysis/run", data={"csrf": csrf}).status_code
        == 422
    )
    seed(client)
    job = enqueue(client)
    assert (
        client.post("/patients/1/ai-analysis/run", data={"csrf": "wrong"}).status_code
        == 403
    )
    assert (
        client.post(
            "/patients/1/ai-analysis/run", data={"csrf": csrf, "patient_id": "2"}
        ).status_code
        == 422
    )
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(2,'Other')")
        c.execute("INSERT INTO patients(id,name,doctor_id) VALUES(3,'Foreign',2)")
    assert client.get("/patients/3/ai-analysis").status_code == 403
    assert (
        client.post("/patients/3/ai-analysis/run", data={"csrf": csrf}).status_code
        == 403
    )
    assert client.get(f"/api/patients/3/ai-analysis/{job}").status_code == 403
    assert client.get(f"/api/patients/2/ai-analysis/{job}").status_code == 404
    login(client, "patient1")
    assert client.get("/patients/1/ai-analysis").status_code == 403
    assert client.get(f"/api/patients/1/ai-analysis/{job}").status_code == 403
    assert (
        client.post(
            "/patients/1/ai-analysis/run", data={"csrf": token(client)}
        ).status_code
        == 403
    )


def test_snapshot_encryption_and_backup_restore_without_attachments(client, tmp_path):
    login(client, "doctor")
    seed(client)
    enqueue(client)
    path = client.app.state.db_path
    with connect(path) as c:
        raw = c.execute("SELECT snapshot FROM patient_ai_runs").fetchone()[0]
        assert isinstance(raw, bytes) and b"MANUAL-CARD-ALPHA" not in raw
    saved = tmp_path / "backup.db"
    backup(path, saved)
    assert (tmp_path / "backup.db.key").is_file()
    with connect(saved) as c:
        restored = ai.decoded(
            c, saved, c.execute("SELECT * FROM patient_ai_runs").fetchone()
        )
        assert "MANUAL-CARD-ALPHA" in json.dumps(restored["snapshot"])
    key_path(path).unlink()
    with pytest.raises(HTTPException) as exc:
        latest(client)
    assert exc.value.status_code == 503 and not key_path(path).exists()
