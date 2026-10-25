"""Refresh current synthetic facts without generating AI text or exposing drafts."""

import json
import re

import pytest

from app.intake_catalog import VERSION as INTAKE_VERSION
from app.scales import VERSION as SCALE_VERSION
from app.services import aviandr_jobs as jobs, aviandr_data, intake
from app.services.aviandr_ollama import AviandrOllama
from app.storage import connect, now
from tests.test_aviandr_conclusions import sign_in, snapshot
from tests.test_aviandr_research import research, actor, provider


def endpoint(pid):
    return f"/api/patients/{pid}/ai-analysis/aviandr/current"


def current(client, pid, **params):
    response = client.get(endpoint(pid), params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_refresh_is_read_only_and_unchanged_hash_does_not_send_html(
    research, monkeypatch
):
    client, path, pids, _ = research

    def forbidden(*args, **kwargs):
        pytest.fail("Reading current cards must not call or enqueue Ollama")

    monkeypatch.setattr(AviandrOllama, "explain", forbidden)
    monkeypatch.setattr(jobs, "enqueue", forbidden)
    with connect(path) as c:
        before = snapshot(c)
    first = current(client, pids["C"])
    assert first["changed"] and first["html"].count("data-conclusion") == 4
    assert "data-av-live" in first["html"] and "Анкет учтено:" in first["html"]
    again = current(client, pids["C"], checksum=first["checksum"])
    assert again == dict(checksum=first["checksum"], changed=False)
    with connect(path) as c:
        assert before == snapshot(c)
    assert (
        client.get(endpoint(pids["C"]), params={"checksum": "x" * 65}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change, expected",
    [
        ("profile", "Новое описание из синтетической карты"),
        ("phq4", "PHQ-4: 6 → 12"),
        ("age", "Возраст по подтверждённой записи врача: 48"),
    ],
)
def test_refresh_returns_new_facts_even_when_medical_status_stays_unknown(
    research, change, expected
):
    client, path, pids, _ = research
    pid = pids["C"]
    before = current(client, pid)
    with connect(path) as c:
        if change == "profile":
            c.execute(
                "UPDATE patient_profiles SET goals=? WHERE patient_id=?",
                (expected, pid),
            )
        elif change == "phq4":
            aid = c.execute(
                "SELECT id FROM assessments WHERE patient_id=? AND scale='phq4' ORDER BY id DESC LIMIT 1",
                (pid,),
            ).fetchone()[0]
            c.execute("UPDATE answers SET value=3 WHERE assessment_id=?", (aid,))
        else:
            aviandr_data.add_observation(
                c,
                path,
                actor(c),
                pid,
                kind="age",
                key="age",
                value={"value": 48},
                confirmed=True,
                note="Синтетическое уточнение возраста для автопересчёта.",
            )
    after = current(client, pid, checksum=before["checksum"])
    assert after["changed"] and after["checksum"] != before["checksum"]
    assert expected in after["html"]
    assert "Недостаточно проверенных данных" in after["html"]
    assert "действующего проверенного правила показаний нет" in after["html"]


def test_private_intake_draft_is_not_published_until_explicit_submit(research):
    client, path, pids, _ = research
    pid = pids["C"]
    before = current(client, pid)
    marker = "НОВОЕ СИНТЕТИЧЕСКОЕ НАБЛЮДЕНИЕ ПОСЛЕ ОТПРАВКИ"
    with connect(path) as c:
        row = c.execute(
            "SELECT * FROM intake_drafts WHERE patient_id=?", (pid,)
        ).fetchone()
        answers = json.loads(row["answers_json"])
        answers["complaints"]["onset"] = {"status": "answered", "value": marker}
        revision = row["revision"] + 1
        c.execute(
            "UPDATE intake_drafts SET revision=?,answers_json=? WHERE patient_id=?",
            (revision, json.dumps(answers), pid),
        )
    assert current(client, pid, checksum=before["checksum"])["changed"] is False
    with connect(path) as c:
        person = c.execute(
            "SELECT * FROM accounts WHERE patient_id=?", (pid,)
        ).fetchone()
        intake.submit(
            c, pid, person, dict(revision=str(revision), catalog_version=INTAKE_VERSION)
        )
    after = current(client, pid, checksum=before["checksum"])
    assert after["changed"] and marker in after["html"]
    assert f"отправленная врачу версия {revision}" in after["html"]


def test_pending_answers_are_visible_without_becoming_a_completed_score(research):
    client, path, pids, _ = research
    pid = pids["C"]
    before = current(client, pid)
    with connect(path) as c:
        day = aviandr_data.today().isoformat()
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
            (pid, day, day, now()),
        ).lastrowid
        aid = c.execute(
            "INSERT INTO assessments(session_id,patient_id,scale,version,status) VALUES(?,?,'phq4',?,'draft')",
            (sid, pid, SCALE_VERSION),
        ).lastrowid
        c.execute(
            "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,'1',3,'answered',?)",
            (aid, now()),
        )
    partial = current(client, pid, checksum=before["checksum"])
    assert partial["changed"] and "PHQ-4 — 1/4 ответов" in partial["html"]
    assert "итоговые баллы не рассчитаны" in partial["html"]
    with connect(path) as c:
        for item in ("2", "3", "4"):
            c.execute(
                "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,?,3,'answered',?)",
                (aid, item, now()),
            )
        c.execute(
            "UPDATE assessments SET status='completed',completed_at=? WHERE id=?",
            (now(), aid),
        )
    done = current(client, pid, checksum=partial["checksum"])
    assert done["changed"] and "PHQ-4: 6 → 12" in done["html"]
    assert "PHQ-4 — 1/4 ответов" not in done["html"]


def test_refresh_hides_stale_model_result_after_card_change(research):
    client, path, pids, _ = research
    pid = pids["C"]
    with connect(path) as c:
        ident = jobs.enqueue(c, path, actor(c), pid)
    jobs.process_one(path, provider([]))
    before = current(client, pid, aviandr_id=ident)
    assert "data-aviandr-result" in before["html"]
    with connect(path) as c:
        c.execute(
            "UPDATE patient_profiles SET goals='Синтетические новые жалобы после объяснения' WHERE patient_id=?",
            (pid,),
        )
    after = current(client, pid, checksum=before["checksum"], aviandr_id=ident)
    assert (
        "data-aviandr-stale" in after["html"]
        and "data-aviandr-result" not in after["html"]
    )
    assert after["html"].count("data-conclusion") == 4


def test_refresh_checks_current_doctor_scope_consent_and_selected_job(research):
    client, path, pids, credentials = research
    own = current(client, pids["C"])
    assert client.get(endpoint(pids["E"])).status_code == 403
    assert (
        client.get(
            endpoint(pids["C"]),
            params={"checksum": own["checksum"], "aviandr_id": 999999},
        ).status_code
        == 404
    )
    with connect(path) as c:
        c.execute("UPDATE consents SET accepted=0 WHERE patient_id=?", (pids["C"],))
    assert (
        client.get(
            endpoint(pids["C"]), params={"checksum": own["checksum"]}
        ).status_code
        == 403
    )
    sign_in(client, "patient-e", credentials["patient-e"], "patient")
    assert client.get(endpoint(pids["E"])).status_code == 403
    sign_in(client, "specialist-2", credentials["specialist-2"])
    assert current(client, pids["E"])["changed"]
    assert client.get(endpoint(pids["D"])).status_code == 403
