"""Source selection permits paraphrases without model-authored quotations."""

import json

import pytest

from app.services import meeting_ai as ai
from app.services import meetings as service
from app.storage import connect
from tests.test_meeting_summary_errors import transport
from tests.test_meeting_summary_contract import response, show_response
from tests.test_meetings import (
    SyntheticModels,
    grants,
    meeting,
    queue,
    recording_id,
    upload,
)


def draft(source_id, text="В разговоре договорились наблюдать за сном."):
    return dict(
        summary=[dict(text=text, source_id=source_id)], next_steps=[], clarifications=[]
    )


@pytest.mark.parametrize("length", [6, 1200, 1201, 2399, 2401, 3999, 4000])
def test_long_sources_keep_every_character_and_original_segment_id(length):
    text = ("Ёлка — сон.\n" * 400)[:length]
    segments = [dict(id=71, text=text)]
    sources = ai.summary_sources(segments)
    assert "".join(s["text"] for s in sources) == text
    for source in sources:
        assert 6 <= len(source["text"].strip()) <= 1200
        item = ai.resolve_summary_sources(
            draft(source["source_id"]), sources, segments
        )["summary"][0]
        assert item["segment_ids"] == [71]
        assert item["quote"] in text


def test_short_replies_get_real_nearby_context_without_fabrication():
    segments = [
        dict(id=7, text="Да."),
        dict(id=9, text="Обсудили время следующей встречи."),
        dict(id=11, text="Нет."),
    ]
    sources = ai.summary_sources(segments)
    assert len(sources) == 3
    assert sources[0]["segment_ids"] == [7, 9]
    assert sources[-1]["segment_ids"] == [9, 11]
    for source in sources:
        item = ai.resolve_summary_sources(
            draft(source["source_id"]), sources, segments
        )["summary"][0]
        original = " ".join(
            s["text"] for s in segments if s["id"] in item["segment_ids"]
        )
        assert item["quote"] in original


def test_free_paraphrase_is_accepted_and_punctuation_is_copied_from_source(monkeypatch):
    original = "Участник: «Буду отмечать, как сплю», — сказал он.\nЕсть ещё вопрос."
    segments = [dict(id=55, text=original)]
    paraphrase = "Участник планирует вести наблюдения за сном."

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        payload = json.loads(request.content)
        source = json.loads(payload["messages"][-1]["content"])[0]
        assert source == {
            "source_id": 1,
            "text": original,
            "speaker": "Говорящий не определён",
        }
        assert (
            "quote"
            not in payload["format"]["properties"]["summary"]["items"]["properties"]
        )
        return response(draft(source["source_id"], paraphrase))

    transport(monkeypatch, handler)
    result, _ = ai.LocalModels().summarize(segments, lambda: None)
    item = result["summary"][0]
    assert item["text"] == paraphrase and paraphrase not in original
    assert item["quote"] == original and item["segment_ids"] == [55]


@pytest.mark.parametrize("source_id", [99999, True, "1", None, 2])
def test_source_must_belong_to_this_part_and_have_integer_type(source_id):
    segments = [
        dict(id=7, text="Первый исходный фрагмент."),
        dict(id=21, text="Второй исходный фрагмент."),
    ]
    sources = ai.summary_sources(segments)
    with pytest.raises(ai.ProcessingError) as caught:
        ai.resolve_summary_sources(draft(source_id), sources[:1], segments)
    assert caught.value.validation_reason == "source_id"


def test_server_source_still_has_to_match_real_transcript():
    segments = [dict(id=7, text="Настоящий текст разговора.")]
    sources = [dict(source_id=1, text="PRIVATE FABRICATED SOURCE", segment_ids=[7])]
    with pytest.raises(ai.ProcessingError) as caught:
        ai.resolve_summary_sources(draft(1), sources, segments)
    assert caught.value.validation_reason == "quote_not_found"
    assert "PRIVATE" not in str(caught.value)


def test_empty_intro_part_does_not_cancel_later_conclusions(monkeypatch):
    monkeypatch.setenv("COMPASS_SUMMARY_CHUNK_CHARS", "6000")
    segments = [
        dict(id=7, text="Вступительная часть. " * 190),
        dict(id=21, text="Договорились отмечать сон. " * 145),
    ]
    calls, progress = [], []

    def checkpoint():
        pass

    checkpoint.progress = lambda **fields: progress.append(fields)

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        sources = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(sources)
        return response(
            dict(summary=[], next_steps=[], clarifications=[])
            if len(calls) == 1
            else draft(sources[0]["source_id"])
        )

    transport(monkeypatch, handler)
    result, _ = ai.LocalModels().summarize(segments, checkpoint)
    assert len(calls) == 2
    assert [p["units_done"] for p in progress] == [0, 1, 2]
    assert result["summary"][0]["segment_ids"] == [21]


def test_all_empty_parts_are_reported_without_forcing_a_made_up_result(monkeypatch):
    calls = []

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        calls.append(request)
        return response(dict(summary=[], next_steps=[], clarifications=[]))

    transport(monkeypatch, handler)
    with pytest.raises(ai.ProcessingError, match="empty_summary"):
        ai.LocalModels().summarize(
            [dict(id=7, text="Проверка связи перед началом.")], lambda: None
        )
    assert len(calls) == 1


def test_native_adapter_saves_paraphrase_as_unpublished_traceable_draft(
    client, meeting, monkeypatch
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
            return show_response()
        return response(draft(2))

    transport(monkeypatch, handler)
    assert service.process_one(client.app.state.db_path, ai.LocalModels())
    with connect(client.app.state.db_path) as c:
        artifact = c.execute(
            "SELECT * FROM meeting_artifacts WHERE kind='summary'"
        ).fetchone()
        item = json.loads(artifact["payload"])["summary"][0]
        assert item["quote"] == "Договорились записывать время сна."
        assert item["segment_ids"] == [2]
        assert item["text"] != item["quote"]
        assert service.published(c, rid) is None
        assert (
            c.execute(
                "SELECT status FROM meeting_jobs WHERE kind='summary'"
            ).fetchone()[0]
            == "done"
        )
