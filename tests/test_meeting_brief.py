"""Whole-meeting compression uses all parts and keeps original evidence/private drafts."""

import json
import pytest
from app.services import meeting_ai as ai
from tests.test_meeting_summary_contract import response, show_response
from tests.test_meeting_summary_errors import transport
from tests.test_meeting_summary_sources import draft
from tests.helpers import login


def test_long_meeting_reduces_notes_and_keeps_late_decision_with_real_evidence(
    monkeypatch,
):
    segments = [
        dict(
            id=i,
            text=(f"Тема {i}. " * 100)
            + ("ПОСЛЕДНИЙ ШАГ: обсудить режим сна." if i == 80 else ""),
        )
        for i in range(1, 81)
    ]
    seen_original, stages, modes, final_input = [], [], [], []

    def checkpoint():
        pass

    checkpoint.progress = lambda **fields: stages.append(fields)

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        payload = json.loads(request.content)
        sources = json.loads(payload["messages"][-1]["content"])
        is_final = payload["format"]["properties"]["summary"]["maxItems"] == 3
        mode = "final" if is_final else "notes"
        modes.append(mode)
        if not sources[0]["speaker"].startswith("Промежуточная заметка"):
            seen_original.extend(s["source_id"] for s in sources)
        assert sum(len(s["text"]) + 150 for s in sources) <= 3000
        selected = next(
            (s for s in sources if "ПОСЛЕДНИЙ ШАГ" in s["text"]), sources[0]
        )
        marker = (
            "ПОСЛЕДНИЙ ШАГ: обсудить режим сна."
            if "ПОСЛЕДНИЙ ШАГ" in selected["text"]
            else selected["text"][:60]
        )
        if is_final:
            final_input.extend(sources)
        return response(draft(selected["source_id"], marker))

    transport(monkeypatch, handler)
    result, _ = ai.LocalModels().summarize(segments, checkpoint)
    assert seen_original == [s["source_id"] for s in ai.summary_sources(segments)]
    assert "condense_summary" in {p["stage"] for p in stages}
    assert modes[-1] == "final" and modes.count("final") == 1
    assert any("ПОСЛЕДНИЙ ШАГ" in s["text"] for s in final_input)
    item = result["summary"][0]
    assert item["segment_ids"] == [80]
    assert item["quote"] in segments[-1]["text"] and item["quote"] != item["text"]
    assert len(result["summary"]) == 1
    assert stages[-1]["units_done"] == stages[-1]["units_total"] == len(modes)
    assert ai.validate_summary(result, segments) == result


@pytest.mark.parametrize("violation", ["items", "characters", "words"])
def test_verbose_final_is_regenerated_once_instead_of_truncating_or_saving(
    monkeypatch, violation
):
    segments = [dict(id=9, text="Разговор о сне и следующей встрече.")]
    calls = []
    bad = draft(1)
    if violation == "items":
        bad["summary"] *= 4
    elif violation == "characters":
        bad["summary"][0]["text"] = "С" * 1001
    else:
        bad["summary"] = [dict(text="Сон. " * 134, source_id=1) for _ in range(3)]

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        calls.append(json.loads(request.content))
        return response(
            bad
            if len(calls) == 1
            else draft(1, "Обсудили сон. Следующая встреча пока не согласована.")
        )

    transport(monkeypatch, handler)
    result, _ = ai.LocalModels().summarize(segments, lambda: None)
    assert len(calls) == 2
    assert "400 слов" in calls[1]["messages"][0]["content"]
    assert result["summary"][0]["text"].endswith("пока не согласована.")


def test_repeated_verbosity_fails_with_safe_reason(monkeypatch):
    def handler(request):
        return (
            show_response()
            if request.url.path == "/api/show"
            else response(draft(1, "С" * 1001))
        )

    transport(monkeypatch, handler)
    with pytest.raises(ai.ProcessingError) as caught:
        ai.LocalModels().summarize(
            [dict(id=1, text="Исходная короткая запись.")], lambda: None
        )
    assert caught.value.validation_reason == "brief_length"
    assert caught.value.code == "summary_too_long"


def test_duplicate_notes_are_combined_without_losing_other_topics():
    def item(text, sid):
        return dict(
            text=text, quote=f"Исходная реплика {sid}", segment_ids=[sid], included=True
        )

    notes = ai.note_sources(
        [
            dict(
                summary=[item("Обсудили сон.", 1), item(" Обсудили  СОН. ", 2)],
                next_steps=[],
                clarifications=[],
            ),
            dict(
                summary=[item("Обсудили тревогу.", 3)], next_steps=[], clarifications=[]
            ),
        ]
    )
    assert len(notes) == 2
    assert notes[-1]["segment_ids"] == [3]
    assert notes[0]["quote"] == "Исходная реплика 1"


def test_permission_checkpoint_stops_before_whole_meeting_final_generation(monkeypatch):
    stage, calls = "", []

    def checkpoint():
        if stage == "finalize_summary":
            raise ai.ProcessingError("permission_changed")

    def progress(**fields):
        nonlocal stage
        stage = fields["stage"]

    checkpoint.progress = progress

    def handler(request):
        if request.url.path == "/api/show":
            return show_response()
        sources = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(sources)
        return response(draft(sources[0]["source_id"]))

    transport(monkeypatch, handler)
    segments = [dict(id=i, text=f"Обсудили тему {i}. " * 100) for i in range(1, 5)]
    with pytest.raises(ai.ProcessingError, match="permission_changed"):
        ai.LocalModels().summarize(segments, checkpoint)
    assert len(calls) > 1
    assert all(
        not part[0]["speaker"].startswith("Промежуточная заметка") for part in calls
    )


def test_doctor_navigation_is_single_panel_inside_header_and_routes_remain(client):
    login(client, "doctor")
    page = client.get("/doctor").text
    header = page.split('<header class="site-header">')[1].split("</header>")[0]
    assert page.count('aria-label="Навигация специалиста"') == 1
    assert 'aria-label="Навигация специалиста"' in header
    for path, label in [
        ("/doctor", "Пациенты"),
        ("/doctor/calendar", "Календарь"),
        ("/doctor/tasks", "Задачи"),
        ("/doctor/resources", "Ресурсы"),
        ("/settings", "Настройки"),
    ]:
        assert f'href="{path}"' in header and label in header
        assert client.get(path).status_code == 200
    for path in ("/clinical-tools", "/medicines", "/doctor/faq"):
        assert f'href="{path}"' in header
        assert client.get(path).status_code == 200
    assert header.count('href="/settings"') == 1
    login(client, "patient1")
    patient = client.get("/patients/1/diary").text
    assert 'aria-label="Навигация специалиста"' not in patient
    assert 'aria-label="Навигация пациента"' in patient
    assert 'href="/help-now"' in patient
