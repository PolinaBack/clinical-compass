"""Unique summaries, a collapsed numbered recording list, and transcript search."""

from copy import deepcopy
from html.parser import HTMLParser
import json

import pytest

from app.services import meetings as service
from app.services.meeting_summary import unique_summary
from app.storage import connect
from tests.helpers import login
from tests.test_meetings import (
    meeting,
    processed,
    grants,
    upload,
    recording_id,
    queue,
    SyntheticModels,
    audio,
)


def item(text, included=True):
    return dict(
        text=text,
        included=included,
        quote="Исходная дословная цитата.",
        segment_ids=[1],
    )


def test_duplicate_points_and_sentences_are_removed_without_mutating_source():
    data = dict(
        summary=[
            item("Обсудили сон. Обсудили сон. Пациент отметил улучшение сна."),
            item("Пациент отметил улучшение сна."),
            item("Записывать время сна."),
            item("  ОБСУДИЛИ   СОН!  "),
        ],
        next_steps=[item("Записывать время сна."), item("Записывать время сна!")],
        clarifications=[item("Пациент отметил улучшение сна.")],
    )
    before = deepcopy(data)
    output = unique_summary(data)
    assert data == before
    assert [v["text"] for v in output["summary"]] == [
        "Обсудили сон. Пациент отметил улучшение сна."
    ]
    assert [v["text"] for v in output["next_steps"]] == ["Записывать время сна."]
    assert output["clarifications"] == []
    assert output["summary"][0]["quote"] == before["summary"][0]["quote"]
    assert output["summary"][0]["segment_ids"] == [1]
    assert unique_summary(output) == output


@pytest.mark.parametrize(
    "first,second",
    [
        ("Пациент принимает препарат.", "Пациент не принимает препарат."),
        ("Названа доза 1,5 мг.", "Названа доза 15 мг."),
        ("Обсудили встречу 28.10.2026.", "Обсудили встречу 29.10.2026."),
        ("Сон длится меньше 6 часов.", "Сон длится больше 6 часов."),
        ("Сон стал лучше.", "Сон стал лучше?"),
    ],
)
def test_distinct_negations_numbers_and_dates_are_never_merged(first, second):
    data = dict(summary=[item(first), item(second)], next_steps=[], clarifications=[])
    assert [v["text"] for v in unique_summary(data)["summary"]] == [first, second]


def test_excluded_and_private_items_cannot_suppress_a_visible_summary():
    data = dict(
        summary=[item("Обсудили сон.")],
        next_steps=[item("Обсудили сон.", False)],
        clarifications=[item("Обсудили сон.")],
    )
    output = unique_summary(data, included_only=True)
    assert [v["text"] for v in output["summary"]] == ["Обсудили сон."]
    assert output["next_steps"] == output["clarifications"] == []
    short = "Да. Пациент уточнил вопрос о сне. Да."
    assert (
        unique_summary(dict(summary=[item(short)], next_steps=[], clarifications=[]))[
            "summary"
        ][0]["text"]
        == short
    )


def test_repeated_sentence_with_distinct_source_context_is_retained():
    first = item("Обсудили состояние матери. Сон ухудшился.")
    second = item("Обсудили состояние отца. Сон ухудшился.")
    second.update(
        quote="Другая исходная реплика о другом родственнике.", segment_ids=[2]
    )
    data = dict(summary=[first, second], next_steps=[], clarifications=[])
    assert unique_summary(data) == data


class RecordingList(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.records = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and "data-meeting-recording" in attrs:
            self.records.append(attrs)


def test_recording_numbers_are_local_stable_and_initially_collapsed(client, meeting):
    mid, context, _ = meeting
    grants(client, context)
    assert upload(client, mid).status_code == 303
    first = recording_id(client)
    with connect(client.app.state.db_path) as c:
        c.execute(
            """INSERT INTO meeting_recordings(id,meeting_id,filename,mime,duration,sha256,content,created_by,created_at,expires_at)
                     SELECT 70,meeting_id,'second.wav',mime,duration,'synthetic-second-sha',content,created_by,created_at,expires_at
                     FROM meeting_recordings WHERE id=?""",
            (first,),
        )
    for path in (f"/meetings/{mid}", f"/doctor/calendar?patient_id=1&meeting_id={mid}"):
        page = client.get(path)
        assert page.status_code == 200
        assert [r["number"] for r in page.context["records"]] == [1, 2]
        records = RecordingList(page.text).records
        assert [r["id"] for r in records] == [f"recording-{first}", "recording-70"]
        assert all("open" not in r for r in records)
        assert "Запись 1" in page.text and "Запись 2" in page.text
        assert "Запись 70" not in page.text and "/export" not in page.text
    assert upload(client, mid, content=audio()[:-1] + b"\1").status_code == 303
    page = client.get(f"/meetings/{mid}")
    assert [r["number"] for r in page.context["records"]] == [1, 2, 3]
    chosen = client.get(f"/meetings/{mid}?recording=70")
    assert [r["id"] for r in RecordingList(chosen.text).records if "open" in r] == [
        "recording-70"
    ]


def test_legacy_summary_display_is_unique_while_history_and_editor_stay_unchanged(
    client, meeting
):
    rid = processed(client, meeting)
    path = client.app.state.db_path
    with connect(path) as c:
        source = service.latest(c, rid, "transcript")
        data = json.loads(service.latest(c, rid, "summary")["payload"])
        data["summary"] *= 2
        ident = service.save_artifact(
            c, rid, "summary", data, "Синтетический прежний итог", source=source["id"]
        )
        raw = service.latest(c, rid, "summary")["payload"]
    for url in (
        f"/meetings/{meeting[0]}",
        f"/doctor/calendar?patient_id=1&meeting_id={meeting[0]}",
    ):
        page = client.get(url)
        current = page.context["records"][0]["summary"]
        assert current["id"] == ident
        assert len(current["data"]["summary"]) == 2
        assert len(current["display"]["summary"]) == 1
        assert page.text.count('name="summary_1_text"') == 1
        brief = page.text.split('<div class="meeting-brief">', 1)[1].split("</div>", 1)[
            0
        ]
        assert brief.count("Обсуждался сон.") == 1
    with connect(path) as c:
        assert service.latest(c, rid, "summary")["payload"] == raw


def test_worker_saves_unique_summary_and_search_does_not_expose_patient_transcript(
    client, meeting
):
    mid, context, _ = meeting
    grants(client, context)
    upload(client, mid)
    rid = recording_id(client)
    queue(client, rid, "transcript")
    assert service.process_one(client.app.state.db_path, SyntheticModels())

    class Repeated(SyntheticModels):
        def summarize(self, segments, checkpoint):
            data, name = super().summarize(segments, checkpoint)
            data["summary"] *= 2
            return data, name

    queue(client, rid, "summary")
    assert service.process_one(client.app.state.db_path, Repeated())
    with connect(client.app.state.db_path) as c:
        assert (
            len(json.loads(service.latest(c, rid, "summary")["payload"])["summary"])
            == 1
        )
        tid = service.latest(c, rid, "transcript")["id"]
    page = client.get(f"/meetings/{mid}")
    assert (
        "Найти фразу в расшифровке" in page.text and "data-transcript-text" in page.text
    )
    history = client.get(f"/meeting-artifacts/{tid}")
    assert history.status_code == 200 and "data-transcript-search" in history.text
    redirected = queue(client, rid, "summary")
    assert f"recording={rid}#recording-{rid}" in redirected.headers["location"]
    login(client, "patient1")
    page = client.get(f"/meetings/{mid}")
    assert (
        "data-transcript-search" not in page.text
        and "data-transcript-text" not in page.text
    )
    assert client.get(f"/meeting-artifacts/{tid}").status_code == 403
    assert client.get(f"/meeting-recordings/{rid}/export").status_code == 404
