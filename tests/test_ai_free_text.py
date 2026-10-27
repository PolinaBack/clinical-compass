"""Opt-in unvalidated transcripts remain distinct from calculated clinical data."""

import json
import re

import httpx
import pytest

from app.experimental_ai.free_text import unverified_text
from app.experimental_ai.ollama import Ollama
from app.experimental_ai import ollama as general_ollama
from app.experimental_ai.store import ExperimentError
from app.services import aviandr_jobs, aviandr_ollama, patient_ai
from app.storage import connect
from tests.helpers import token
from tests.test_aviandr_research import research
from tests.test_aviandr_conclusions import snapshot
from tests.test_ai_formatting import visible_text


def endpoint(pid, kind):
    return (
        f"/patients/{pid}/ai-analysis"
        + ("/aviandr" if kind == "aviandr" else "")
        + "/run"
    )


def queue(research, kind, mode="free"):
    client, path, pids, _ = research
    pid = pids["C"]
    result = client.post(
        endpoint(pid, kind),
        data=dict(csrf=token(client), response_mode=mode),
        follow_redirects=False,
    )
    assert result.status_code == 303, result.text
    table = "aviandr_runs" if kind == "aviandr" else "patient_ai_runs"
    with connect(path) as c:
        return c.execute(
            f"SELECT * FROM {table} WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,)
        ).fetchone()


def provider(kind, text, calls, *, done=True, reason="stop", failure=None):
    def respond(req):
        calls.append(req)
        if req.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        if failure == "connect":
            raise httpx.ConnectError("private error", request=req)
        return httpx.Response(
            200, json=dict(done=done, done_reason=reason, message=dict(content=text))
        )

    cls = aviandr_ollama.AviandrOllama if kind == "aviandr" else Ollama
    return cls(httpx.MockTransport(respond))


def run(research, kind, transport):
    module = aviandr_jobs if kind == "aviandr" else patient_ai
    module.process_one(research[1], transport)
    table = "aviandr_runs" if kind == "aviandr" else "patient_ai_runs"
    with connect(research[1]) as c:
        row = c.execute(
            f"SELECT * FROM {table} WHERE patient_id=? ORDER BY id DESC LIMIT 1",
            (research[2]["C"],),
        ).fetchone()
        return patient_ai.decoded(c, research[1], row)


@pytest.mark.parametrize("kind", ["general", "aviandr"])
@pytest.mark.parametrize(
    "text",
    [
        "Короткий собственный ответ модели.\nВторая строка без обязательной структуры.",
        '{"summary": "Незавершённый JSON без обязательных полей"',
        "В карте упомянут Авиандр.\n<script>window.__aiInjected=1</script>\nhttps://example.invalid/",
    ],
)
def test_free_text_bypasses_output_checks_but_preserves_privacy_and_calculations(
    research, monkeypatch, kind, text
):
    client, path, pids, _ = research
    with connect(path) as c:
        before = snapshot(c)

    def rejected(*args):
        raise AssertionError("Output validator must not run for free text")

    module = aviandr_ollama if kind == "aviandr" else general_ollama
    monkeypatch.setattr(module, "validate", rejected)
    queue(research, kind)
    calls = []
    job = run(research, kind, provider(kind, text, calls))
    assert job["status"] == "completed" and job["error_code"] is None
    assert job["result"] == dict(output_format="free_text", text=text, incomplete=False)
    assert job["provenance"]["response_mode"] == "free"
    assert job["provenance"]["output_validation"] == "not_checked"
    assert job["provenance"]["provider"] == "mock-http-test-only"
    payload = json.loads(calls[-1].content)
    assert "format" not in payload
    assert "обычным" in payload["messages"][0]["content"]
    assert calls[-1].url.host == "127.0.0.1"
    html = client.get(f"/patients/{pids['C']}/ai-analysis").text
    assert " ".join(text.split()) in visible_text(html)
    assert "Непроверенный ответ модели" in html
    assert ("Краткая сводка" if kind == "general" else "Свободный ответ модели") in html
    assert "<script>window.__aiInjected=1</script>" not in html
    assert html.count('class="aviandr-direction"') == 4
    with connect(path) as c:
        after = snapshot(c)
        for table, rows in before.items():
            if table not in {"audit", "patient_ai_runs", "aviandr_runs"}:
                assert after[table] == rows, table
        table = "aviandr_runs" if kind == "aviandr" else "patient_ai_runs"
        encrypted = c.execute(
            f"SELECT result FROM {table} WHERE id=?", (job["id"],)
        ).fetchone()[0]
        assert text.encode() not in encrypted


@pytest.mark.parametrize("kind", ["general", "aviandr"])
@pytest.mark.parametrize("done,reason", [(True, "length"), (False, None)])
def test_partial_response_is_preserved_and_marked_incomplete(
    research, kind, done, reason
):
    queue(research, kind)
    text = "Полученное начало ответа, даже если продолжение не сформировано."
    job = run(research, kind, provider(kind, text, [], done=done, reason=reason))
    assert job["status"] == "completed"
    assert job["result"]["text"] == text and job["result"]["incomplete"]
    assert (
        "Получена только часть ответа"
        in research[0].get(f"/patients/{research[2]['C']}/ai-analysis").text
    )


@pytest.mark.parametrize("kind", ["general", "aviandr"])
@pytest.mark.parametrize(
    "failure,code", [("connect", "provider_unavailable"), ("empty", "empty_response")]
)
def test_no_response_remains_a_real_error_without_prepared_text(
    research, kind, failure, code
):
    queue(research, kind)
    job = run(research, kind, provider(kind, "", [], failure=failure))
    assert job["status"] == "error" and job["error_code"] == code
    assert job["result"] is None
    assert (
        "data-unverified-ai"
        not in research[0].get(f"/patients/{research[2]['C']}/ai-analysis").text
    )


@pytest.mark.parametrize("kind", ["general", "aviandr"])
def test_mode_is_explicit_idempotent_and_switching_creates_a_new_job(research, kind):
    first = queue(research, kind, "structured")
    assert queue(research, kind, "structured")["id"] == first["id"]
    second = queue(research, kind, "free")
    assert first["id"] != second["id"]
    assert queue(research, kind, "free")["id"] == second["id"]
    table = "aviandr_runs" if kind == "aviandr" else "patient_ai_runs"
    with connect(research[1]) as c:
        assert (
            c.execute(
                f"SELECT error_code FROM {table} WHERE id=?", (first["id"],)
            ).fetchone()[0]
            == "response_mode_changed"
        )


@pytest.mark.parametrize("kind", ["general", "aviandr"])
def test_response_mode_never_bypasses_csrf_or_accepts_extra_card_data(research, kind):
    client, path, pids, _ = research
    url = endpoint(pids["C"], kind)
    assert (
        client.post(url, data=dict(csrf="wrong", response_mode="free")).status_code
        == 403
    )
    assert (
        client.post(
            url, data=dict(csrf=token(client), response_mode="unknown")
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, data=dict(csrf=token(client), response_mode="free", text="injected")
        ).status_code
        == 422
    )
    assert (
        client.post(
            url,
            content="csrf="
            + token(client)
            + "&response_mode=free&response_mode=structured",
            headers={"content-type": "application/x-www-form-urlencoded"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"message": {}},
        {"message": {"content": " "}},
        {"message": {"content": ["text"]}},
    ],
)
def test_non_text_payload_is_not_invented(envelope):
    with pytest.raises(ExperimentError, match="empty_response"):
        unverified_text(envelope)


def test_refresh_buttons_force_a_new_get_without_changing_snapshot(research):
    client, path, pids, _ = research
    queue(research, "aviandr")
    with connect(path) as c:
        before = snapshot(c)
    first = client.get(f"/patients/{pids['C']}/ai-analysis").text
    second = client.get(f"/patients/{pids['C']}/ai-analysis").text
    a = re.findall(r'name="refresh" value="([a-f0-9]+)"', first)
    b = re.findall(r'name="refresh" value="([a-f0-9]+)"', second)
    assert len(a) == len(b) == 2 and a[0] == a[1] and b[0] == b[1]
    assert a[0] != b[0]
    with connect(path) as c:
        assert snapshot(c) == before
