"""Synthetic-only technical fixtures. No fixture is evidence about a medicine."""

import io
import json
import secrets
import sqlite3
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import httpx
import openpyxl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import connect, now
from app.experimental_ai.scenarios import digest
from app.experimental_ai.store import ExperimentError
from app.services import (
    aviandr_analysis as analysis,
    aviandr_data as data,
    aviandr_sources as sources,
    aviandr_jobs as jobs,
)
from app.services import drug_library as library, medicine_drafts, medicine_reactions
from app.services.aviandr_ollama import AviandrOllama, validate, input_data
from app.services.clinical_attachments import key_path
from scripts.setup_aviandr_scenarios import seed, setup
from scripts.backup import backup
from tests.helpers import login, token, TEST_PASSWORD
from starlette.datastructures import FormData


@pytest.fixture
def research(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        result = seed(c, client.app.state.db_path)
        pids = {
            r["scenario"]: r["patient_id"]
            for r in c.execute("SELECT * FROM aviandr_scenarios")
        }
    return client, client.app.state.db_path, pids, dict(result["credentials"])


def actor(c, did=1):
    return c.execute(
        "SELECT * FROM accounts WHERE role='doctor' AND doctor_id=? ORDER BY id LIMIT 1",
        (did,),
    ).fetchone()


def report(research, letter="C"):
    client, path, pids, _ = research
    with connect(path) as c:
        return analysis.build(
            c, path, actor(c, 2 if letter == "E" else 1), pids[letter]
        )


def fixture_source(c, user, *, verify=True, level="caution"):
    """Deliberately fake source, isolated in a test DB, never shipped as evidence."""
    w = openpyxl.Workbook()
    s = w.active
    s.title = "Сведения"
    s.append(["Поле", "Текст", "Раздел/страница"])
    for field in ("indications", "contraindications", "warnings", "adverse"):
        s.append(
            [
                field,
                "ТЕХНИЧЕСКАЯ ФИКСТУРА: не медицинские сведения; " + field,
                "ТЕСТ, раздел " + field,
            ]
        )
    s = w.create_sheet("Взаимодействия")
    s.append(["Препарат A", "Препарат B", "Уровень", "Объяснение", "Раздел/страница"])
    s.append(
        [
            "D01",
            "D11",
            level,
            "ТЕХНИЧЕСКАЯ ФИКСТУРА ПАРЫ — не медицинское свойство",
            "ТЕСТ, раздел пары",
        ]
    )
    stream = io.BytesIO()
    w.save(stream)
    meta = dict(
        drug_code="D01",
        source_url="https://grls.rosminzdrav.ru/TEST-NOT-A-REAL-SOURCE",
        version="ТЕСТОВАЯ ФИКСТУРА",
        registration=sources.registration()["registration"],
        product_form="ТЕСТОВАЯ ФОРМА",
        checked_on=date.today().isoformat(),
        valid_until=(date.today() + timedelta(days=30)).isoformat(),
    )
    iid = library.preview(
        c,
        "TEST-ONLY.xlsx",
        stream.getvalue(),
        user["id"],
        official=library.official_payload(c, stream.getvalue(), "TEST-ONLY.xlsx", meta),
    )
    library.commit(c, iid)
    if verify:
        c.execute(
            "INSERT INTO drug_source_events(import_id,action,actor_id,note,created_at) VALUES(?,'verify',?,'ТЕСТОВАЯ СВЕРКА, НЕ МЕДИЦИНСКИЕ ДАННЫЕ',?)",
            (iid, user["id"], now()),
        )
    return iid


def rule(
    c,
    user,
    iid,
    kind="contraindication",
    fact="factor",
    key="TEST-FACTOR",
    expected="present",
    operator="equals",
):
    return sources.add_rule(
        c,
        user,
        dict(
            import_id=iid,
            kind=kind,
            fact=fact,
            key=key,
            expected=expected,
            operator=operator,
            label="ТЕСТОВЫЙ ФАКТОР, НЕ КЛИНИЧЕСКОЕ ПРАВИЛО",
            quote="ТЕХНИЧЕСКАЯ ФИКСТУРА: не медицинские сведения; "
            + sources.FIELDS[kind],
        ),
    )


def answer(r):
    items = []
    for direction in r["directions"]:
        ref = next(e["id"] for e in r["evidence"] if e["direction"] == direction["key"])
        items.append(
            dict(
                direction=direction["key"],
                text="В переданном расчёте описаны сохранённые сведения и конкретные пробелы.",
                evidence_ids=[ref],
            )
        )
    return dict(
        summary=[
            dict(
                text="Сохранённые сведения синтетической карты требуют проверки врачом перед решением.",
                evidence_ids=[r["evidence"][0]["id"]],
            )
        ],
        explanations=items,
        questions=[
            dict(
                gap_id=g["id"],
                text="Что можно уточнить врачу по этому конкретному пробелу записанных сведений?",
            )
            for g in r["gaps"][:3]
        ],
        contradictions=[],
    )


def provider(calls, *, failure=None):
    def respond(req):
        calls.append(req)
        if failure == "connect":
            raise httpx.ConnectError("offline", request=req)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=req)
        if req.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        payload = json.loads(req.content)
        # The generated fixture references exactly the transmitted evidence IDs.
        supplied = json.loads(payload["messages"][1]["content"])
        r = dict(
            directions=[dict(key=s["direction"]) for s in supplied["statuses"]],
            evidence=supplied["evidence"],
            gaps=supplied["gaps"],
        )
        result = answer(r)
        if failure == "unsafe":
            result["summary"][0][
                "text"
            ] = "Препарат безопасен, назначить 20 мг ежедневно."
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"content": json.dumps(result, ensure_ascii=False)},
            },
        )

    return AviandrOllama(httpx.MockTransport(respond))


def test_three_actual_scenarios_and_full_questionnaires_without_fabricated_rules(
    research,
):
    _, path, pids, _ = research
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 90
        assert c.execute("SELECT count(*) FROM aviandr_scenarios").fetchone()[0] == 3
        assert len(sources.rules(c)) == 0
    c, d, e = (report(research, k) for k in "CDE")
    assert (
        c["phase"] == "not_current"
        and d["phase"] == "unknown"
        and e["phase"] == "current"
    )
    assert c["directions"][0]["status"] == analysis.INDICATIONS["unknown"]
    assert d["pairs"][0]["state"] == "unknown"
    assert e["adherence"]["taken"] == 14 and e["adherence"]["missed"] == 2
    assert e["adherence"]["unrecorded"] == 0 and len(e["data"]["reactions"]) == 1
    assert (
        e["adherence"]["before_course_days"] == 14
        and e["adherence"]["expected_days"] == 16
    )
    assert (
        e["changes"]["wellbeing"]["n_before"] == 14
        and e["changes"]["wellbeing"]["n_after"] == 16
    )
    assert e["data"]["preference_choices"][0]["primary"] is True
    for r in (c, d, e):
        assert len(r["data"]["questionnaires"]) == 9
        assert sum(len(q["answers"]) for q in r["data"]["questionnaires"]) == 60
        assert len(r["trajectories"]) == 3 and 3 <= len(r["gaps"]) <= 7
        assert r["registration"]["registration_until"] == "2028-05-31"
        assert r["registration"]["promotes_medical_rules"] is False
        assert not r["sources"] and r["rules_count"] == 0


def test_rules_require_active_source_correct_section_and_doctor_confirmation(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        iid = fixture_source(c, user, verify=False)
        with pytest.raises(HTTPException):
            rule(c, user, iid)
        c.execute(
            "INSERT INTO drug_source_events(import_id,action,actor_id,note,created_at) VALUES(?,'verify',?,'TEST ONLY',?)",
            (iid, user["id"], now()),
        )
        ident = rule(c, user, iid)
        assert rule(c, user, iid) == ident
        with pytest.raises(HTTPException):
            sources.add_rule(
                c,
                user,
                dict(
                    import_id=iid,
                    kind="contraindication",
                    fact="factor",
                    key="TEST",
                    expected="present",
                    operator="equals",
                    label="TEST",
                    quote="ВЫДУМАННАЯ ЦИТАТА",
                ),
            )
        data.add_observation(
            c,
            path,
            user,
            pids["C"],
            kind="factor",
            key="TEST-FACTOR",
            value=dict(state="present"),
            confirmed=False,
            note="Техническая запись со слов для проверки состояния.",
        )
    r = report(research)
    assert r["directions"][1]["level"] == "critical"
    with connect(path) as c:
        data.add_observation(
            c,
            path,
            actor(c),
            pids["C"],
            kind="factor",
            key="TEST-FACTOR",
            value=dict(state="present"),
            confirmed=True,
            note="Техническое подтверждение фикстуры, не клинический факт.",
        )
    assert report(research)["directions"][1]["level"] == "critical"
    with connect(path) as c:
        c.execute(
            "INSERT INTO drug_source_events(import_id,action,actor_id,note,created_at) VALUES(?,'withdraw',?,'TEST withdrawal',?)",
            (iid, user["id"], now()),
        )
    assert report(research)["directions"][1]["level"] == "attention"


def test_screening_never_confirms_indication_without_a_documented_diagnosis(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        iid = fixture_source(c, user)
        rule(c, user, iid, kind="indication", fact="diagnosis", key="TEST-DIAGNOSIS")
    assert (
        report(research)["directions"][0]["status"] == analysis.INDICATIONS["clarify"]
    )
    with connect(path) as c:
        data.add_observation(
            c,
            path,
            user,
            pids["C"],
            kind="diagnosis",
            key="TEST-DIAGNOSIS",
            value=dict(state="present"),
            confirmed=True,
            note="Техническая фикстура ранее записанного диагноза.",
        )
    assert report(research)["directions"][0]["status"] == analysis.INDICATIONS["match"]


def test_pair_needs_confirmed_identification_and_verified_specific_rule(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        fixture_source(c, user, level="contraindicated")
    assert report(research, "D")["pairs"][0]["state"] == "unknown"
    with connect(path) as c:
        data.add_observation(
            c,
            path,
            user,
            pids["D"],
            kind="therapy",
            key="D11",
            value=dict(
                state="current",
                start="",
                inn=library.current(c, "drug")["D11"]["inn"],
                identity_confirmed=True,
            ),
            confirmed=True,
            note="Техническая идентификация для проверки фикстуры пары.",
        )
    pair = report(research, "D")["pairs"][0]
    assert (
        pair["state"] == "contraindicated"
        and pair["known"][0]["source"]["locator"] == "ТЕСТ, раздел пары"
    )


def test_unsaved_or_saved_private_draft_is_not_current_therapy(research):
    client, path, pids, _ = research
    with connect(path) as c:
        medicine_drafts.save(
            c,
            path,
            actor(c),
            pids["C"],
            FormData(
                [
                    ("revision", "0"),
                    ("drug", "D02"),
                    ("D02.dose", "ТЕСТОВАЯ ДОЗА"),
                    ("D02.frequency", "1 раз в день"),
                ]
            ),
        )
    r = report(research)
    assert not any(t["code"] == "D02" for t in r["data"]["therapy"])
    assert not r["pairs"]
    assert r["phase"] == "not_current"


def test_real_transport_schema_provenance_and_unchanged_statuses(research):
    _, path, pids, _ = research
    with connect(path) as c:
        ident = jobs.enqueue(c, path, actor(c), pids["C"])
    before = report(research)
    calls = []
    assert jobs.process_one(path, provider(calls)) == ident
    with connect(path) as c:
        row = c.execute("SELECT * FROM aviandr_runs WHERE id=?", (ident,)).fetchone()
        result = jobs.decoded(c, path, row)
    assert (
        row["status"] == "completed"
        and result["provenance"]["provider"] == "mock-http-test-only"
    )
    assert [r.url.path for r in calls] == ["/api/show", "/api/chat"]
    request = json.loads(calls[-1].content)
    assert request["format"]["additionalProperties"] is False
    assert "questionnaires" in request["messages"][1]["content"]
    assert before["directions"] == report(research)["directions"]
    assert result["snapshot"]["checksum"] == before["checksum"]


@pytest.mark.parametrize(
    "failure,code",
    [
        ("connect", "provider_unavailable"),
        ("timeout", "timeout"),
        ("unsafe", "unsafe_response"),
    ],
)
def test_provider_failure_keeps_calculated_report_and_no_fake_text(
    research, failure, code
):
    client, path, pids, _ = research
    with connect(path) as c:
        ident = jobs.enqueue(c, path, actor(c), pids["C"])
    jobs.process_one(path, provider([], failure=failure))
    with connect(path) as c:
        row = c.execute("SELECT * FROM aviandr_runs WHERE id=?", (ident,)).fetchone()
        if failure == "unsafe":
            result = jobs.decoded(c, path, row)
            assert row["status"] == "completed" and not row["error_code"]
            assert result["result"]["output_format"] == "free_text"
            assert "назначить 20 мг" in result["result"]["text"]
            assert result["provenance"]["output_validation"] == "not_checked"
            assert (
                "data-aviandr-result"
                in client.get(f"/patients/{pids['C']}/ai-analysis").text
            )
            return
        assert (
            row["status"] == "error"
            and row["error_code"] == code
            and row["result"] is None
        )
    page = client.get(f"/patients/{pids['C']}/ai-analysis")
    assert page.status_code == 200 and "data-aviandr-analysis" in page.text
    assert "data-aviandr-result" not in page.text
    assert "Объяснение не получено" in page.text


@pytest.mark.parametrize(
    "change",
    ["extra_status", "wrong_ref", "wrong_direction", "wrong_gap", "too_few_questions"],
)
def test_server_rejects_unbound_structured_output(research, change):
    r = report(research)
    raw = answer(r)
    if change == "extra_status":
        raw["status"] = "safe"
    elif change == "wrong_ref":
        raw["summary"][0]["evidence_ids"] = ["INVENTED"]
    elif change == "wrong_direction":
        raw["explanations"][0]["evidence_ids"] = [
            raw["explanations"][1]["evidence_ids"][0]
        ]
    elif change == "wrong_gap":
        raw["questions"][0]["gap_id"] = "INVENTED"
    else:
        raw["questions"] = raw["questions"][:2]
    with pytest.raises(ExperimentError):
        validate(raw, r)


def test_stale_data_and_revoked_access_stop_before_calling_model(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        ident = jobs.enqueue(c, path, user, pids["C"])
        data.add_observation(
            c,
            path,
            user,
            pids["C"],
            kind="factor",
            key="NEW-FACTOR",
            value=dict(state="unknown"),
            note="Новое уточнение после создания снимка.",
        )
    calls = []
    jobs.process_one(path, provider(calls))
    assert not calls
    with connect(path) as c:
        assert (
            c.execute(
                "SELECT error_code FROM aviandr_runs WHERE id=?", (ident,)
            ).fetchone()[0]
            == "card_changed"
        )
        ident = jobs.enqueue(c, path, user, pids["C"])
        c.execute(
            "UPDATE accounts SET auth_version=auth_version+1 WHERE id=?", (user["id"],)
        )
    jobs.process_one(path, provider(calls))
    assert not calls


def test_roles_csrf_daily_records_and_invalid_forms(research):
    client, path, pids, credentials = research
    assert client.get(f"/patients/{pids['E']}/ai-analysis").status_code == 403
    assert (
        client.post(
            f"/patients/{pids['C']}/ai-analysis/aviandr/run", data={"csrf": "bad"}
        ).status_code
        == 403
    )
    login(client, "patient1")
    assert client.get(f"/patients/{pids['C']}/ai-analysis").status_code == 403
    with connect(path) as c:
        person = c.execute(
            "SELECT * FROM accounts WHERE patient_id=?", (pids["E"],)
        ).fetchone()
        with pytest.raises(HTTPException):
            data.add_observation(
                c,
                path,
                person,
                pids["C"],
                kind="intake_event",
                key="D01",
                value=dict(state="taken", draft_id="1", revision="1"),
            )
        with pytest.raises(HTTPException):
            data.add_observation(
                c,
                path,
                actor(c),
                pids["C"],
                kind="age",
                key="age",
                value=dict(value="-1"),
                note="Тест неверного возраста",
            )


def test_context_is_not_mutated_and_keeps_complete_input_trace(research):
    r = report(research, "E")
    original = digest(r)
    value = input_data(r)
    assert digest(r) == original
    assert len(value["questionnaires"]) == 3
    assert sum(len(q["answers"]) for q in value["questionnaires"]) == 20


def test_seed_repeat_preserves_manual_edits_and_has_two_separate_rosters(research):
    _, path, pids, _ = research
    with connect(path) as c:
        before = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "patients",
                "accounts",
                "diary",
                "assessments",
                "answers",
                "aviandr_observations",
            )
        }
        c.execute(
            "UPDATE patient_profiles SET goals=? WHERE patient_id=?",
            ("Ручное уточнение", pids["C"]),
        )
        result = seed(c, path)
        assert not result["created"] and not result["credentials"]
        assert before == {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before
        }
        assert (
            c.execute(
                "SELECT goals FROM patient_profiles WHERE patient_id=?", (pids["C"],)
            ).fetchone()[0]
            == "Ручное уточнение"
        )
        assert (
            c.execute(
                "SELECT doctor_id FROM patients WHERE id=?", (pids["E"],)
            ).fetchone()[0]
            == 2
        )


def test_explicit_reset_backs_up_prior_data_and_keeps_main_passwords(client):
    login(client, "doctor")
    path = client.app.state.db_path
    with connect(path) as c:
        c.execute(
            "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(1,'2026-01-01',1,5,'','OLD-SYNTHETIC-ROW',0)"
        )
        old = {
            r["username"]: r["password_hash"]
            for r in c.execute("SELECT * FROM accounts")
        }
    result = setup(path, reset=True)
    assert Path(result["backup"]).is_file()
    with connect(result["backup"]) as c:
        assert c.execute(
            "SELECT 1 FROM diary WHERE note='OLD-SYNTHETIC-ROW'"
        ).fetchone()
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM patients").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 90
        assert not c.execute(
            "SELECT 1 FROM diary WHERE note='OLD-SYNTHETIC-ROW'"
        ).fetchone()
        assert all(
            c.execute(
                "SELECT password_hash FROM accounts WHERE username=?", (name,)
            ).fetchone()[0]
            == hashed
            for name, hashed in old.items()
        )
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    # Normal repeated setup is additive and cannot erase this new database.
    assert not setup(path)["created"]


def test_backup_and_lost_key_do_not_replace_protected_research_data(research, tmp_path):
    _, path, pids, _ = research
    destination = tmp_path / "copy.db"
    backup(path, destination)
    assert Path(str(destination) + ".key").is_file()
    with connect(destination) as c:
        assert analysis.build(c, destination, actor(c), pids["C"])["rules_count"] == 0
    key = key_path(path)
    saved = key.read_bytes()
    key.unlink()
    try:
        with connect(path) as c:
            with pytest.raises(HTTPException) as exc:
                analysis.build(c, path, actor(c), pids["C"])
            assert exc.value.status_code == 503
        assert not key.exists()
    finally:
        key.write_bytes(saved)


def test_submitted_allergy_is_provisional_until_that_version_is_reviewed(research):
    _, path, pids, _ = research
    pid = pids["C"]
    with connect(path) as c:
        user = actor(c)
        iid = fixture_source(c, user)
        rule(c, user, iid, fact="allergy", key="TEST-ALLERGEN")
        raw = {
            "name": {"status": "answered", "value": "TEST-ALLERGEN"},
            "type": {"status": "answered", "value": "Аллергия"},
        }
        person = c.execute(
            "SELECT * FROM accounts WHERE patient_id=?", (pid,)
        ).fetchone()
        eid = c.execute(
            "INSERT INTO intake_entities(patient_id,kind,version,data_json,updated_by,updated_at) VALUES(?,'reaction',1,?,?,?)",
            (pid, json.dumps(raw), person["id"], now()),
        ).lastrowid
    assert not any(f["kind"] == "allergy" for f in report(research)["data"]["facts"])
    with connect(path) as c:
        original = c.execute(
            "SELECT * FROM intake_submissions WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (pid,),
        ).fetchone()
        value = json.loads(original["payload"])
        value["entities"].append(dict(id=eid, kind="reaction", version=1, data=raw))
        value["revision"] = original["revision"] + 1
        sid = c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (
                pid,
                value["revision"],
                original["catalog_version"],
                json.dumps(value),
                person["id"],
                now(),
            ),
        ).lastrowid
    assert report(research)["directions"][1]["level"] == "critical"
    with connect(path) as c:
        c.execute(
            "INSERT INTO intake_reviews(submission_id,target,status,note,actor_id,created_at) VALUES(?,?,'confirmed','TEST ONLY',?,?)",
            (sid, f"entity:{eid}:1", user["id"], now()),
        )
    assert report(research)["directions"][1]["level"] == "critical"


def test_conflicting_confirmed_age_records_require_clarification(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        iid = fixture_source(c, user)
        rule(c, user, iid, fact="age", key="age", expected="18", operator="lt")
        sid = c.execute(
            "SELECT id FROM intake_submissions WHERE patient_id=?", (pids["C"],)
        ).fetchone()[0]
        c.execute(
            "INSERT INTO intake_reviews(submission_id,target,status,note,actor_id,created_at) VALUES(?,'answer:personal.birth_date','confirmed','TEST AGE REVIEW',?,?)",
            (sid, user["id"], now()),
        )
        data.add_observation(
            c,
            path,
            user,
            pids["C"],
            kind="age",
            key="age",
            value=dict(value=17),
            confirmed=True,
            note="ТЕСТ противоречивых записей, не клинический сценарий.",
        )
    result = report(research)
    assert result["directions"][1]["level"] == "attention"
    assert any("противоречат" in g["reason"] for g in result["all_gaps"])


def test_mismatching_inn_does_not_produce_a_confirmed_pair(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        fixture_source(c, user, level="contraindicated")
        data.add_observation(
            c,
            path,
            user,
            pids["D"],
            kind="therapy",
            key="D11",
            value=dict(
                state="current",
                start="",
                inn="НЕСОВПАДАЮЩЕЕ ВЕЩЕСТВО",
                identity_confirmed=True,
            ),
            confirmed=True,
            note="Техническая проверка ошибочной идентификации.",
        )
    result = report(research, "D")
    assert result["pairs"][0]["state"] == "unknown"
    assert result["data"]["therapy"][0]["identity_consistent"] is False


def test_reference_registration_alone_never_creates_clinical_rules(research):
    result = report(research)
    assert result["registration"]["holder"] == "ООО «Авинейро», Россия"
    assert result["registration"]["manufacturer"] is None
    assert result["registration"]["official_document_url"] is None
    assert result["rules_count"] == 0 and not result["sources"]
    assert result["directions"][0]["level"] == "attention"


def test_observation_retry_cannot_silently_change_confirmation_or_date(research):
    _, path, pids, _ = research
    with connect(path) as c:
        user = actor(c)
        nonce = secrets.token_urlsafe(24)
        args = dict(
            kind="factor",
            key="TEST-RETRY",
            value=dict(state="present"),
            note="Техническая запись для повтора формы.",
            token=nonce,
        )
        first = data.add_observation(c, path, user, pids["C"], **args)
        assert data.add_observation(c, path, user, pids["C"], **args) == first
        with pytest.raises(HTTPException):
            data.add_observation(c, path, user, pids["C"], confirmed=True, **args)
        with pytest.raises(HTTPException):
            data.add_observation(
                c,
                path,
                user,
                pids["C"],
                day=(date.today() - timedelta(days=1)).isoformat(),
                **args,
            )


def test_reset_invalidates_old_patient_cookie_even_if_id_becomes_doctor(client):
    login(client, "patient1")
    path = client.app.state.db_path
    with connect(path) as c:
        c.execute(
            "UPDATE accounts SET active=0,auth_version=22 WHERE username='patient2'"
        )
    setup(path, reset=True)
    with connect(path) as c:
        assert c.execute("SELECT min(auth_version) FROM accounts").fetchone()[0] > 22
    assert client.get("/doctor", follow_redirects=False).status_code in (303, 401, 403)
    client.cookies.clear()
    login(client, "patient1")
    assert client.get("/doctor").status_code == 403
