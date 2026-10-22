"""Slow local generation has a bounded, configurable wait and smaller full-coverage parts."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import pytest

from app.services import meeting_ai as ai
from scripts import setup_meeting_ai as script
from tests.test_meeting_summary_errors import transport
from tests.test_meeting_summary_contract import response, show_response
from tests.test_meeting_summary_sources import draft


def test_default_wait_and_chunk_size(monkeypatch):
    monkeypatch.delenv("COMPASS_SUMMARY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("COMPASS_SUMMARY_CHUNK_CHARS", raising=False)
    assert ai.summary_options() == dict(timeout_seconds=900, chunk_chars=3000)


@pytest.mark.parametrize("timeout,chars", [(60, 1500), (3600, 6000)])
def test_configured_boundaries(monkeypatch, timeout, chars):
    monkeypatch.setenv("COMPASS_SUMMARY_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("COMPASS_SUMMARY_CHUNK_CHARS", str(chars))
    assert ai.summary_options() == dict(timeout_seconds=timeout, chunk_chars=chars)


@pytest.mark.parametrize(
    "name,value",
    [
        ("COMPASS_SUMMARY_TIMEOUT_SECONDS", "0"),
        ("COMPASS_SUMMARY_TIMEOUT_SECONDS", "3601"),
        ("COMPASS_SUMMARY_TIMEOUT_SECONDS", "not a number"),
        ("COMPASS_SUMMARY_CHUNK_CHARS", "1499"),
        ("COMPASS_SUMMARY_CHUNK_CHARS", "6001"),
        ("COMPASS_SUMMARY_CHUNK_CHARS", "not a number"),
    ],
)
def test_invalid_settings_fail_before_model_request(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ai.ProcessingError) as caught:
        ai.LocalModels().summarize(
            [dict(id=1, text="Искусственный разговор о сне.")], lambda: None
        )
    assert caught.value.code == "summary_configuration_invalid"
    assert caught.value.stage == "configuration"


@pytest.mark.parametrize(
    "name,value",
    [
        ("COMPASS_SUMMARY_TIMEOUT_SECONDS", "1800"),
        ("COMPASS_SUMMARY_CHUNK_CHARS", "1500"),
    ],
)
def test_pending_job_configuration_guard_includes_limits(monkeypatch, name, value):
    previous = ai.config_stamp()
    monkeypatch.setenv(name, value)
    assert ai.config_stamp() != previous


def test_actual_request_gets_longer_read_wait_and_all_sources_are_processed(
    monkeypatch,
):
    segments = [
        dict(id=7, text="Разговор о сне. " * 240),
        dict(id=21, text="Обсудили планы. " * 240),
    ]
    calls, progress = [], []

    def checkpoint():
        pass

    checkpoint.progress = lambda **fields: progress.append(fields)

    def handler(request):
        timeouts = request.extensions["timeout"]
        assert timeouts["connect"] == 5
        if request.url.path == "/api/show":
            assert timeouts["read"] == 10
            return show_response()
        assert timeouts == dict(connect=5, read=900, write=30, pool=5)
        sources = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert sum(len(s["text"]) + 150 for s in sources) <= 3000
        calls.append(sources)
        return response(draft(sources[0]["source_id"]))

    transport(monkeypatch, handler)
    result, _ = ai.LocalModels().summarize(segments, checkpoint)
    assert len(calls) > len(list(ai.chunks(ai.summary_sources(segments), limit=6000)))
    original_parts = [
        part
        for part in calls
        if not part[0]["speaker"].startswith("Промежуточная заметка")
    ]
    assert [s["source_id"] for part in original_parts for s in part] == [
        s["source_id"] for s in ai.summary_sources(segments)
    ]
    assert (
        len(result["summary"]) == 1
    )  # A single whole-meeting result, not concatenated notes.
    assert progress[-1] == dict(
        stage="finalize_summary", units_done=len(calls), units_total=len(calls)
    )
    assert all(p["units_total"] == len(calls) for p in progress)


@pytest.mark.parametrize("read_seconds,succeeds", [(0.04, False), (1.0, True)])
def test_real_httpx_accepts_slow_reply_within_limit_and_still_times_out(
    monkeypatch, read_seconds, succeeds
):
    """Real HTTP on artificial data; short timings stand in for minutes, no model/DB."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/api/show":
                payload = {"model_info": {"general.architecture": "qwen2"}}
            else:
                time.sleep(0.15)
                payload = {"done": True, "message": {"content": json.dumps(draft(1))}}
            body = json.dumps(payload).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("COMPASS_OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(
        ai,
        "summary_options",
        lambda: dict(timeout_seconds=read_seconds, chunk_chars=3000),
    )
    try:

        def run():
            return ai.LocalModels().summarize(
                [dict(id=7, text="Искусственный разговор о сне.")], lambda: None
            )

        if succeeds:
            result, _ = run()
            assert result["summary"][0]["segment_ids"] == [7]
        else:
            with pytest.raises(ai.ProcessingError) as caught:
                run()
            assert (
                caught.value.code == "summary_timeout"
                and caught.value.stage == "/api/chat"
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_summary_diagnostic_shows_effective_limits(monkeypatch, capsys):
    def handler(request):
        return (
            show_response() if request.url.path == "/api/show" else response(draft(1))
        )

    transport(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "900 с" in output and "3000 символов" in output
