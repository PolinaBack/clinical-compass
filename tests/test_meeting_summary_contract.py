"""Free paraphrases retain real source excerpts, consent checks and bounded repair."""

import json

import httpx
import pytest

from app.services import meeting_ai as ai
from app.services import meetings as service
from app.storage import connect
from scripts import setup_meeting_ai as script
from tests.test_meeting_summary_errors import SEGMENTS, transport
from tests.test_meetings import (
    SyntheticModels,
    grants,
    meeting,
    queue,
    recording_id,
    upload,
)


def result(segment=None):
    segment = segment or {"source_id": 1}
    return dict(
        summary=[
            dict(
                text="Обсуждён сон.",
                source_id=segment["source_id"],
            )
        ],
        next_steps=[],
        clarifications=[],
    )


def response(payload):
    return httpx.Response(
        200,
        json={
            "done": True,
            "message": {"content": json.dumps(payload, ensure_ascii=False)},
        },
    )


def show_response():
    return httpx.Response(200, json={"model_info": {"general.architecture": "qwen2"}})


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({}, "groups"),
        (dict(summary="ошибка", next_steps=[], clarifications=[]), "category_items"),
        (dict(summary=[{}], next_steps=[], clarifications=[]), "item_fields"),
        (dict(summary=[None], next_steps=[], clarifications=[]), "item_fields"),
        (
            dict(
                summary=[dict(text=12, quote=SEGMENTS[0]["text"], segment_ids=[1])],
                next_steps=[],
                clarifications=[],
            ),
            "text",
        ),
        (
            dict(
                summary=[dict(text="Итог", quote="нет", segment_ids=[1])],
                next_steps=[],
                clarifications=[],
            ),
            "quote",
        ),
        (
            dict(
                summary=[
                    dict(text="Итог", quote=SEGMENTS[0]["text"], segment_ids=["1"])
                ],
                next_steps=[],
                clarifications=[],
            ),
            "segment_ids",
        ),
        (
            dict(
                summary=[
                    dict(text="Итог", quote=SEGMENTS[0]["text"], segment_ids=[True])
                ],
                next_steps=[],
                clarifications=[],
            ),
            "segment_ids",
        ),
        (
            dict(
                summary=[
                    dict(text="Итог", quote=SEGMENTS[0]["text"], segment_ids=[99])
                ],
                next_steps=[],
                clarifications=[],
            ),
            "segment_ids",
        ),
        (
            dict(
                summary=[
                    dict(text="Итог", quote="PRIVATE INVENTED QUOTE", segment_ids=[1])
                ],
                next_steps=[],
                clarifications=[],
            ),
            "quote_not_found",
        ),
    ],
)
def test_validator_reports_safe_reason_without_loosening_evidence(payload, reason):
    with pytest.raises(ai.ProcessingError) as caught:
        ai.validate_summary(payload, SEGMENTS)
    assert caught.value.code == "invalid_summary"
    assert caught.value.validation_reason == reason
    assert "PRIVATE" not in str(caught.value)


def test_schema_uses_original_ids_in_each_chunk_and_counts_only_validated_parts(
    monkeypatch,
):
    segments = [
        dict(id=7, text="Обсудили сон. " * 280),
        dict(id=21, text="Обсудили активность. " * 190),
    ]
    calls, progress = [], []

    def checkpoint():
        pass

    checkpoint.progress = lambda **fields: progress.append(fields)

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        payload = json.loads(request.content)
        part = json.loads(payload["messages"][-1]["content"])
        calls.append(part)
        schema = payload["format"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(ai.GROUPS)
        for category in schema["properties"].values():
            item = category["items"]
            assert item["additionalProperties"] is False
            assert item["required"] == ["text", "source_id"]
            ids = item["properties"]["source_id"]
            assert ids == {"type": "integer", "enum": [s["source_id"] for s in part]}
            assert all(set(s) == {"source_id", "text", "speaker"} for s in part)
        return response(result(part[0]))

    transport(monkeypatch, handler)
    output, _ = ai.LocalModels().summarize(segments, checkpoint)
    assert len(calls) == 2
    assert [item["segment_ids"] for item in output["summary"]] == [[7], [21]]
    assert [p["units_done"] for p in progress] == [0, 1, 2]
    assert all(p["units_total"] == 2 for p in progress)


@pytest.mark.parametrize("failure", ["quote", "ids", "fields", "json"])
def test_one_repair_uses_same_source_and_strict_validation(monkeypatch, failure):
    calls = []
    bad = result()
    if failure == "quote":
        bad["summary"][0]["quote"] = "PRIVATE INVENTED QUOTE"
    elif failure == "ids":
        bad["summary"][0]["source_id"] = 99999
    elif failure == "fields":
        bad = {"wrong": "PRIVATE MODEL OUTPUT"}

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        payload = json.loads(request.content)
        calls.append(payload)
        assert json.loads(payload["messages"][-1]["content"]) == [
            {
                "source_id": 1,
                "text": SEGMENTS[0]["text"],
                "speaker": "Говорящий не определён",
            }
        ]
        assert "PRIVATE" not in json.dumps(payload)
        if len(calls) == 1:
            if failure == "json":
                return httpx.Response(
                    200, json={"message": {"content": "PRIVATE NON JSON"}}
                )
            return response(bad)
        assert (
            "Предыдущая попытка не прошла проверку" in payload["messages"][0]["content"]
        )
        return response(result())

    transport(monkeypatch, handler)
    output, _ = ai.LocalModels().summarize(SEGMENTS, lambda: None)
    assert len(calls) == 2
    assert output["summary"][0]["quote"] == SEGMENTS[0]["text"]
    assert output["summary"][0]["included"] is True


def test_invalid_evidence_is_not_returned_after_two_attempts_and_diagnostic_names_rule(
    monkeypatch, capsys
):
    calls = []

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        calls.append(request)
        bad = result()
        bad["summary"][0]["source_id"] = 99999
        return response(bad)

    transport(monkeypatch, handler)
    with pytest.raises(SystemExit) as caught:
        script.main(["--check-summary"])
    assert caught.value.code == 1 and len(calls) == 2
    output = capsys.readouterr().out
    assert "HTTP: 200" in output and "Правило: source_id" in output
    assert "PRIVATE" not in output


def test_permission_change_prevents_repair_request(monkeypatch):
    calls = []
    checked_bad_response = False

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        calls.append(request)
        return response({})

    def checkpoint():
        nonlocal checked_bad_response
        if calls:
            if checked_bad_response:
                raise ai.ProcessingError("permission_changed")
            checked_bad_response = True

    transport(monkeypatch, handler)
    with pytest.raises(ai.ProcessingError, match="permission_changed"):
        ai.LocalModels().summarize(SEGMENTS, checkpoint)
    assert len(calls) == 1


def test_failed_repair_saves_no_summary_and_logs_only_safe_reason(
    client, meeting, monkeypatch, caplog
):
    mid, context, _ = meeting
    grants(client, context)
    assert upload(client, mid).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    assert queue(client, rid, "summary").status_code == 303
    calls = []

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        calls.append(request)
        bad = result()
        bad["summary"][0]["source_id"] = 99999
        return response(bad)

    transport(monkeypatch, handler)
    assert service.process_one(client.app.state.db_path, ai.LocalModels())
    assert len(calls) == 2
    assert "validation=source_id" in caplog.text and "PRIVATE" not in caplog.text
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM meeting_artifacts WHERE kind='summary'"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM meeting_artifacts WHERE kind='transcript'"
            ).fetchone()[0]
            == 1
        )
        assert c.execute(
            "SELECT status,error_code FROM meeting_jobs WHERE kind='summary'"
        ).fetchone()[:] == ("failed", "invalid_summary")
        assert "PRIVATE" not in str(
            [tuple(row) for row in c.execute("SELECT * FROM meeting_events")]
        )
