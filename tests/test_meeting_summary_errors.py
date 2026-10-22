"""Transport, provider, protocol and model-format errors must remain distinct."""

import json

import httpx
import pytest

from app.services import meeting_ai as ai
from scripts import setup_meeting_ai as script
from app.services import meetings as service
from app.storage import connect
from tests.test_meetings import (
    meeting,
    grants,
    upload,
    recording_id,
    queue,
    SyntheticModels,
)

SEGMENTS = [
    dict(id=1, start=0, end=4, text="Обсудили режим сна и время отхода ко сну.")
]


def transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("connect", "summary_provider_unavailable"),
        ("timeout", "summary_timeout"),
        ("404", "summary_model_missing"),
        ("400", "summary_request_rejected"),
        ("500", "summary_provider_http_error"),
        ("envelope", "summary_protocol_invalid"),
        ("message", "summary_protocol_invalid"),
        ("json", "summary_json_invalid"),
        ("truncated", "summary_output_truncated"),
        ("citations", "invalid_summary"),
    ],
)
def test_errors_from_working_show_and_chat_are_not_all_unavailable(
    monkeypatch, scenario, code
):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["format"] == ai.summary_schema(
            ai.summary_sources(SEGMENTS), mode="final"
        )
        assert payload["options"]["num_ctx"] == 8192
        assert payload["options"]["num_predict"] == 2400
        if scenario == "connect":
            raise httpx.ConnectError("PRIVATE ERROR CONTENT", request=request)
        if scenario == "timeout":
            raise httpx.ReadTimeout("PRIVATE ERROR CONTENT", request=request)
        if scenario.isdigit():
            return httpx.Response(
                int(scenario), json={"error": "PRIVATE ERROR CONTENT"}
            )
        if scenario == "envelope":
            return httpx.Response(200, content=b"not JSON")
        if scenario == "message":
            return httpx.Response(200, json={"message": None})
        if scenario == "truncated":
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "done_reason": "length",
                    "message": {"content": '{"summary":['},
                },
            )
        if scenario == "json":
            return httpx.Response(
                200, json={"message": {"content": "PRIVATE NON JSON CONTENT"}}
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        dict(
                            summary=[
                                dict(
                                    text="Тестовый итог",
                                    source_id=99999,
                                )
                            ],
                            next_steps=[],
                            clarifications=[],
                        )
                    )
                }
            },
        )

    transport(monkeypatch, handler)
    with pytest.raises(ai.ProcessingError) as caught:
        ai.LocalModels().summarize(SEGMENTS, lambda: None)
    error = caught.value
    assert error.code == code
    assert error.stage in ("/api/chat", "validate_summary")
    assert not error.server_error and "PRIVATE" not in str(error)


def test_synthetic_summary_diagnostic_shows_server_reason_without_database(
    monkeypatch, capsys
):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        prompt = json.loads(request.content)["messages"][-1]["content"]
        assert (
            "Тестовый участник" in prompt
            and "Договорились записывать время сна" in prompt
        )
        return httpx.Response(500, json={"error": "SYNTHETIC MODEL MEMORY ERROR"})

    transport(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary"])
    assert caught.value.code == 1
    output = capsys.readouterr().out
    assert "/api/chat" in output and "HTTP: 500" in output
    assert "SYNTHETIC MODEL MEMORY ERROR" in output
    assert calls == ["/api/show", "/api/chat"]


def test_synthetic_summary_diagnostic_checks_full_app_response(monkeypatch, capsys):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "content": json.dumps(
                        dict(
                            summary=[
                                dict(
                                    text="Тестовый итог о сне.",
                                    source_id=1,
                                )
                            ],
                            next_steps=[],
                            clarifications=[],
                        )
                    )
                },
            },
        )

    transport(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "прошли проверку" in output and "Тестовый итог о сне." not in output


def test_summary_check_cannot_trigger_downloads_or_another_check():
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary", "--download-whisper"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary", "--check-transcription"])
    assert caught.value.code == 2


def test_real_job_never_logs_or_stores_ollama_error_body(
    client, meeting, monkeypatch, caplog
):
    mid, context, _ = meeting
    grants(client, context)
    assert upload(client, mid).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    assert queue(client, rid, "summary").status_code == 303

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        return httpx.Response(500, json={"error": "PRIVATE MEETING CONTENT"})

    transport(monkeypatch, handler)
    assert service.process_one(client.app.state.db_path, ai.LocalModels())
    assert "HTTP=500" in caplog.text and "stage=/api/chat" in caplog.text
    assert "PRIVATE MEETING CONTENT" not in caplog.text
    page = client.get(f"/meetings/{mid}").text
    assert "PRIVATE MEETING CONTENT" not in page
    assert "Ollama ответила ошибкой HTTP" in page
    with connect(client.app.state.db_path) as c:
        assert not c.execute(
            "SELECT id FROM meeting_artifacts WHERE kind='summary'"
        ).fetchall()
        assert "PRIVATE MEETING CONTENT" not in str(
            [tuple(row) for row in c.execute("SELECT * FROM meeting_events")]
        )
