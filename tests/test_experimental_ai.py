"""Synthetic-only boundary, arithmetic, actual HTTP contract, durable failures and UI ACL.

Model replies here are explicit transport fixtures, never production fallbacks.
"""

import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.experimental_ai import analysis, jobs, sources, store
from app.experimental_ai.ollama import Ollama, config, validate
from app.experimental_ai.scenarios import trusted, canonical, digest
from tests.helpers import login, token


@pytest.fixture
def demo(tmp_path, monkeypatch):
    path = tmp_path / "experiment.db"
    monkeypatch.setenv("COMPASS_EXPERIMENT_DB", str(path))
    monkeypatch.setenv("COMPASS_EXPERIMENT_SOURCES", str(tmp_path / "sources"))
    monkeypatch.setenv("COMPASS_EXPERIMENT_OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("COMPASS_EXPERIMENT_OLLAMA_MODEL", "qwen2.5:3b")
    store.initialize(path)
    return path


def response():
    return dict(
        summary="В синтетическом дневнике среднее самочувствие к последней неделе выше, чем к первой. Пропуски исключены из расчётов; причинность изменений не установлена.",
        summary_evidence_ids=["F2", "F6"],
        hypotheses=[
            dict(
                text="Связь режима сна и самочувствия можно уточнить в беседе, без вывода о причине.",
                evidence_ids=["F3", "F6"],
            )
        ],
        questions=[
            dict(text="Что помогало отдыхать в последнюю неделю?", evidence_ids=["F7"]),
            dict(
                text="Как менялись трудности с засыпанием?", evidence_ids=["F1", "F3"]
            ),
            dict(
                text="Что отличало напряжённый рабочий день от обычных дней?",
                evidence_ids=["F7"],
            ),
        ],
        material_ids=[],
    )


def provider(handler=None, captured=None):
    def serve(request):
        if captured is not None:
            captured.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        if handler:
            return handler(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": json.dumps(response(), ensure_ascii=False)},
                "eval_count": 124,
            },
        )

    return Ollama(httpx.MockTransport(serve))


def latest(path):
    with store.connect(path) as c:
        return jobs.decoded(
            c.execute(
                "SELECT * FROM experiment_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        )


def test_setup_idempotent_one_patient_30_days_and_no_clinical_file(demo):
    with store.connect(demo) as c:
        data = store.load(c)
        assert data == trusted() and data["patient"]["name"] == "Мария Соколова"
        assert len(data["observations"]) == 30 and len(data["questionnaires"]) == 6
    run_id = jobs.enqueue(demo, 1)
    assert store.initialize(demo) is False
    assert latest(demo)["id"] == run_id


@pytest.mark.parametrize(
    "kind",
    ["patient", "observation", "questionnaire", "manifest", "extra-table", "trigger"],
)
def test_tampered_fixture_never_reaches_provider(demo, kind):
    jobs.enqueue(demo, 1)
    with sqlite3.connect(demo) as c:
        if kind == "patient":
            c.execute(
                "UPDATE experiment_patient SET payload=?",
                (canonical(dict(name="PRIVATE REAL DATA")),),
            )
        elif kind == "observation":
            c.execute(
                "UPDATE experiment_observations SET payload='{}' WHERE day='2026-09-01'"
            )
        elif kind == "questionnaire":
            c.execute("DELETE FROM experiment_questionnaires WHERE ordinal=0")
        elif kind == "manifest":
            c.execute(
                "UPDATE experiment_manifest SET dataset_hash='synthetic-flag-is-not-enough'"
            )
        elif kind == "extra-table":
            c.execute("CREATE TABLE patients(id INTEGER,name TEXT)")
        else:
            c.execute(
                "CREATE TRIGGER unexpected AFTER UPDATE ON experiment_runs BEGIN SELECT 1; END"
            )
    calls = []
    with pytest.raises(store.ExperimentError, match="synthetic_integrity_failed"):
        jobs.process_one(demo, provider(captured=calls))
    assert calls == []


def test_protected_existing_database_alias_and_non_demo(tmp_path, monkeypatch):
    clinical = tmp_path / "clinical.db"
    with sqlite3.connect(clinical) as c:
        c.execute("CREATE TABLE patients(name TEXT)")
        c.execute("INSERT INTO patients VALUES('KEEP')")
    before = clinical.read_bytes()
    monkeypatch.setenv("COMPASS_DB", str(clinical))
    for path in (clinical, tmp_path / "alias.db"):
        if path != clinical:
            path.symlink_to(clinical)
        with pytest.raises(store.ExperimentError, match="clinical_database_forbidden"):
            store.initialize(path)
    monkeypatch.delenv("COMPASS_DB")
    with pytest.raises(store.ExperimentError):
        store.initialize(clinical)
    assert clinical.read_bytes() == before


def test_descriptive_arithmetic_missingness_and_existing_scale_keys(demo):
    result = analysis.analyze(trusted())
    m = result["metrics"]
    assert m["wellbeing"]["n"] == 28 and m["wellbeing"]["missing"] == 2
    assert m["wellbeing"]["first_week"] == 3.57
    assert m["wellbeing"]["last_week"] == 6 and m["wellbeing"]["delta"] == 2.43
    assert m["sleep_hours"]["n"] == 29
    assert [q["total"] for q in result["questionnaires"]] == [12, 11, 8, 8, 5, 5]
    assert result["correlation"]["n"] == 27
    zero = [{"wellbeing": 0, "sleep_hours": 0} for _ in range(30)]
    assert analysis.series_stats(zero, "wellbeing")["mean"] == 0
    assert analysis.correlation(zero)["pearson"] is None
    missing = [{"wellbeing": None} for _ in range(30)]
    assert analysis.series_stats(missing, "wellbeing")["mean"] is None
    assert len(analysis.plot_segments(trusted()["observations"], "wellbeing", 10)) == 3


def test_http_success_grounded_metadata_persistence_and_review(demo):
    calls = []
    run_id = jobs.enqueue(demo, 8)
    assert jobs.enqueue(demo, 8) == run_id
    assert jobs.process_one(demo, provider(captured=calls))
    assert not jobs.process_one(demo, provider())
    row = latest(demo)
    assert row["status"] == "completed" and row["result"] == response()
    assert [r.url.path for r in calls] == ["/api/show", "/api/chat"]
    request = json.loads(calls[-1].content)
    assert (
        request["stream"] is False
        and request["format"]["additionalProperties"] is False
    )
    assert "PRIVATE" not in canonical(request)
    assert "patient_id" not in canonical(request) and "actor_id" not in canonical(
        request
    )
    assert row["provenance"]["dataset_hash"] == digest(trusted())
    assert row["provenance"]["eval_count"] == 124
    with store.connect(demo) as c:
        jobs.review(c, run_id, 8, "2", "1", "2", "unclear")
        jobs.review(c, run_id, 8, "2", "2", "2", "none")
        assert c.execute("SELECT COUNT(*) FROM experiment_reviews").fetchone()[0] == 2
        assert store.load(c) == trusted()
    assert jobs.enqueue(demo, 8) != run_id


@pytest.mark.parametrize(
    "case,code",
    [
        ("404", "model_missing"),
        ("400", "request_rejected"),
        ("500", "provider_http_error"),
        ("redirect", "local_only"),
        ("timeout", "timeout"),
        ("connect", "provider_unavailable"),
        ("json", "invalid_response"),
        ("truncated", "invalid_response"),
        ("oversized", "invalid_response"),
    ],
)
def test_errors_distinct_no_fake_summary_or_leaked_response(demo, case, code):
    def handler(request):
        if case.isdigit():
            return httpx.Response(int(case), json={"error": "PRIVATE DETAILS"})
        if case == "redirect":
            return httpx.Response(307, headers={"location": "https://external.invalid"})
        if case == "timeout":
            raise httpx.ReadTimeout("PRIVATE DETAILS", request=request)
        if case == "connect":
            raise httpx.ConnectError("PRIVATE DETAILS", request=request)
        if case == "json":
            return httpx.Response(200, content=b"PRIVATE DETAILS")
        if case == "oversized":
            return httpx.Response(200, content=b"x" * 140000)
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length",
                "message": {"content": "PRIVATE DETAILS"},
            },
        )

    jobs.enqueue(demo, 1)
    jobs.process_one(demo, provider(handler))
    row = latest(demo)
    assert row["status"] == "error" and row["error_code"] == code
    assert row["result"] is None and row["metrics"] and row["provenance"] is None
    assert "PRIVATE DETAILS" not in canonical(row)


@pytest.mark.parametrize(
    "text",
    [
        "Назначить сертралин 50 мг ежедневно.",
        "Принимайте Авиандр.",
        "Установлен диагноз депрессия.",
        "Change to sertraline 25 mg daily.",
        "Отменить прежнее лечение.",
        "Увеличить дозировку вечером.",
    ],
)
def test_medication_and_diagnostic_assertions_rejected(text):
    raw = response()
    raw["summary"] = text + " Остальные данные приведены только для технического теста."
    with pytest.raises(store.ExperimentError, match="unsafe_response"):
        validate(raw, analysis.analyze(trusted()), [])


@pytest.mark.parametrize(
    "mutation", ["unknown-fact", "unknown-source", "extra-field", "missing-group"]
)
def test_contract_requires_existing_evidence_and_no_extra_treatment_field(mutation):
    raw = response()
    if mutation == "unknown-fact":
        raw["questions"][0]["evidence_ids"] = ["REAL-PATIENT-1"]
    elif mutation == "unknown-source":
        raw["material_ids"] = ["made-up-publication"]
    elif mutation == "extra-field":
        raw["prescription"] = "anything"
    else:
        del raw["questions"]
    with pytest.raises(store.ExperimentError, match="invalid_response"):
        validate(raw, analysis.analyze(trusted()), [])


def test_direct_adapter_rejects_real_data_or_injected_analysis():
    calls = []
    original = trusted()
    changed = copy.deepcopy(original)
    changed["patient"]["complaint"] = "PRIVATE RECORD"
    with pytest.raises(store.ExperimentError):
        provider(captured=calls).generate(changed, analysis.analyze(original), [])
    metrics = analysis.analyze(original)
    metrics["facts"].append(dict(id="F8", text="PRIVATE RECORD"))
    with pytest.raises(store.ExperimentError):
        provider(captured=calls).generate(original, metrics, [])
    assert not calls


def test_uncertainty_statement_is_allowed_without_diagnostic_assertion():
    raw = response()
    raw["summary"] = (
        "Изменения в вымышленных наблюдениях требуют обсуждения. Данных для диагноза недостаточно; причины изменений не установлены."
    )
    assert validate(raw, analysis.analyze(trusted()), [])["summary"] == raw["summary"]


@pytest.mark.parametrize(
    "url",
    [
        "https://external.invalid",
        "http://127.0.0.1:11434/elsewhere",
        "http://user:password@localhost:11434",
        "http://localhost:11434?remote=1",
    ],
)
def test_nonlocal_configs_rejected(monkeypatch, url):
    monkeypatch.setenv("COMPASS_EXPERIMENT_OLLAMA_URL", url)
    with pytest.raises(store.ExperimentError):
        config()


def test_remote_model_and_interrupted_worker(demo, monkeypatch):
    monkeypatch.setenv("COMPASS_EXPERIMENT_OLLAMA_MODEL", "qwen-cloud")
    with pytest.raises(store.ExperimentError):
        config()
    monkeypatch.setenv("COMPASS_EXPERIMENT_OLLAMA_MODEL", "qwen2.5:3b")
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "remote_host": "https://remote",
                "model_info": {"general.architecture": "qwen"},
            },
        )
    )
    jobs.enqueue(demo, 1)
    jobs.process_one(demo, Ollama(transport))
    assert latest(demo)["error_code"] == "local_only"
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(
        timespec="seconds"
    )
    with store.connect(demo) as c:
        c.execute("UPDATE experiment_runs SET status='running',heartbeat_at=?", (old,))
    retry = jobs.enqueue(demo, 1)
    with store.connect(demo) as c:
        assert (
            c.execute("SELECT error_code FROM experiment_runs WHERE id=1").fetchone()[0]
            == "worker_interrupted"
        )
    assert retry == 2


def test_sources_require_exact_version_human_review_and_no_redirects(demo):
    assert not any(s["verified"] for s in sources.catalog())
    body = b"<html><title>Depression in adults: treatment and management</title></html>"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=body, headers={"content-type": "text/html"}
            )
        )
    ) as c:
        meta = sources.fetch("nice-depression", c)
    assert not sources.retrieve(analysis.analyze(trusted()))
    with pytest.raises(store.ExperimentError):
        sources.approve("nice-depression", "WRONG-HASH", "Synthetic Reviewer")
    sources.approve("nice-depression", meta["sha256"], "Synthetic Reviewer")
    assert [s["id"] for s in sources.retrieve(analysis.analyze(trusted()))] == [
        "nice-depression"
    ]

    def selected_source(request):
        raw = response()
        raw["material_ids"] = ["nice-depression"]
        return httpx.Response(
            200, json={"done": True, "message": {"content": json.dumps(raw)}}
        )

    metrics = analysis.analyze(trusted())
    result, provenance = provider(selected_source).generate(
        trusted(), metrics, sources.retrieve(metrics)
    )
    assert result["material_ids"] == ["nice-depression"]
    assert provenance["verified_sources"][0]["sha256"] == meta["sha256"]
    assert provenance["verified_sources"][0]["checked_at"]
    assert (
        provenance["verified_sources"][0]["url"]
        == sources.CATALOG["nice-depression"]["url"]
    )
    (sources.folder() / "nice-depression.html").write_bytes(body + b"changed")
    assert not any(s["verified"] for s in sources.catalog())
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                302, headers={"location": "https://external.invalid"}
            )
        )
    ) as c:
        with pytest.raises(store.ExperimentError):
            sources.fetch("nice-anxiety", c)


def test_ui_synthetic_only_acl_extra_input_csrf_and_main_database_unchanged(
    demo, tmp_path
):
    with TestClient(create_app(tmp_path / "clinical.db")) as client:
        login(client, "doctor")
        page = client.get("/doctor/experimental-ai")
        assert page.status_code == 200 and "Мария Соколова" in page.text
        assert "Тестовый режим · только синтетические данные" in page.text
        assert 'name="patient_id"' not in page.text and 'type="file"' not in page.text
        assert 'id="aviandr-reference"' in page.text and 'href="/reference/aviandr"' in page.text
        assert "ИИ не выбирает препарат и дозировку" in page.text
        csrf = token(client)
        with sqlite3.connect(client.app.state.db_path) as c:
            before = [
                (name, c.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0])
                for (name,) in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        assert (
            client.post(
                "/doctor/experimental-ai/run", data={"csrf": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/doctor/experimental-ai/run", data={"csrf": csrf, "patient_id": "1"}
            ).status_code
            == 422
        )
        assert client.get("/doctor/experimental-ai?patient_id=1").status_code == 422
        result = client.post("/doctor/experimental-ai/run", data={"csrf": csrf})
        assert (
            result.status_code == 200
            and "Ожидает отдельного обработчика" in result.text
        )
        assert client.get("/api/experimental-ai/runs/1").json()["status"] == "queued"
        jobs.process_one(demo, provider())
        assert (
            "Индивидуальные уточняющие вопросы"
            in client.get("/doctor/experimental-ai").text
        )
        exported = client.get("/doctor/experimental-ai/export/1").json()
        assert exported["dataset"] == trusted()
        assert (
            client.post(
                "/doctor/experimental-ai/runs/1/review",
                data=dict(
                    csrf=csrf,
                    grounding="2",
                    usefulness="1",
                    safety="2",
                    issue="unclear",
                ),
            ).status_code
            == 200
        )
        with sqlite3.connect(client.app.state.db_path) as c:
            after = [
                (name, c.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0])
                for (name,) in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        assert before == after
        login(client, "patient1")
        for endpoint in [
            "/doctor/experimental-ai",
            "/api/experimental-ai/runs/1",
            "/doctor/experimental-ai/export/1",
        ]:
            assert client.get(endpoint).status_code == 403
        assert (
            client.post(
                "/doctor/experimental-ai/run", data={"csrf": token(client)}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/doctor/experimental-ai/runs/1/review", data={"csrf": token(client)}
            ).status_code
            == 403
        )
        assert "Открыть эксперимент" not in client.get("/me").text
