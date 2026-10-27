"""Readable clinical-card AI view with actual, unfiltered model transcripts."""

import json
import re
from html import unescape

import httpx
import pytest

from app.services import aviandr_jobs, aviandr_ollama
from app.storage import connect
from tests.helpers import login
from tests.test_patient_ai import seed, enqueue, provider as general_provider
from app.services import patient_ai
from tests.test_aviandr_research import research
from tests.test_ai_free_text import queue, run, provider


def test_summary_follows_status_and_only_requested_trends_remain(client):
    login(client, "doctor")
    seed(client)
    enqueue(client)
    patient_ai.process_one(client.app.state.db_path, general_provider([]))
    page = client.get("/patients/1/ai-analysis")
    assert page.status_code == 200
    html = page.text
    assert re.search(
        r'data-ai-result-area="general">\s*<section.*?</section>\s*<section class="card" id="patient-ai-result">',
        html,
        re.S,
    )
    assert html.index('id="patient-ai-result"') < html.index('id="aviandr"')
    assert re.findall(r'data-trend="([^"]+)"', html) == [
        "anxiety",
        "wellbeing",
        "sleep_hours",
    ]
    assert "data-questionnaire-trend" not in html and "Записанная динамика" not in html
    for removed in [
        "Ответы, требующие отдельного уточнения",
        "Регистрационные сведения ГРЛС",
        "Исходные анкеты и сведения карты",
        "Проверенные источники и правила",
        "История текстовых объяснений",
        "Модель и версия анализа",
        "Сведения, переданные модели",
        "Подробнее",
    ]:
        assert removed not in html
    assert 'href="#fact-' not in html and 'href="#av-evidence-' not in html
    for section in ("av-trends", "av-questions"):
        block = html.split('id="' + section + '"', 1)[1].split("</section>", 1)[0]
        assert "<a " not in block
    assert html.count('name="response_mode" value="free"') == 2
    assert '<select name="response_mode"' not in html
    assert "Добавить уточнённое сведение врача" in html


def test_saved_points_are_connected_without_filling_missing_days(client):
    login(client, "doctor")
    seed(client)
    page = client.get("/patients/1/ai-analysis")
    observations = page.context["av_report"]["data"]["observations"]
    for key in ("wellbeing", "sleep_hours"):
        values = [v for v in observations if v[key] is not None]
        assert len(values) == 3
        block = page.text.split('data-trend="' + key + '"', 1)[1].split(
            "</article>", 1
        )[0]
        assert block.count("<circle ") == len(values)
        assert block.count("data-gap-connection") == 2
        assert 'stroke-dasharray="6 5"' in block
    assert "пунктир отмечает промежутки без записей" in page.text


@pytest.mark.parametrize("kind", ["general", "aviandr"])
@pytest.mark.parametrize(
    "text,done",
    [
        ("Да.", True),
        ("Диагноз: тест. Назначить 20 мг. ТЕХНИЧЕСКАЯ ФИКСТУРА.", True),
        ('{"summary":"Не завершён JSON', False),
    ],
)
def test_legacy_structured_jobs_show_any_nonempty_model_text(
    research, kind, text, done
):
    queue(research, kind, "structured")
    job = run(research, kind, provider(kind, text, [], done=done))
    assert job["status"] == "completed" and job["error_code"] is None
    assert (
        job["result"]["output_format"] == "free_text" and job["result"]["text"] == text
    )
    assert job["provenance"]["output_validation"] == "not_checked"
    assert job["result"]["incomplete"] is (not done)
    page = research[0].get(f'/patients/{research[2]["C"]}/ai-analysis')
    assert text in unescape(page.text)
    assert "Непроверенный ответ модели" in page.text


def test_received_aviandr_text_survives_data_change_during_generation(research):
    client, path, pids, _ = research
    queue(research, "aviandr", "free")
    text = "ТЕХНИЧЕСКИЙ ОТВЕТ по прежнему снимку, без подстановки готовой сводки."

    def serve(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        with connect(path) as c:
            c.execute(
                "UPDATE patient_profiles SET goals='Изменено во время генерации' WHERE patient_id=?",
                (pids["C"],),
            )
        return httpx.Response(200, json=dict(done=True, message=dict(content=text)))

    job = run(
        research, "aviandr", aviandr_ollama.AviandrOllama(httpx.MockTransport(serve))
    )
    assert job["status"] == "completed" and job["result"]["text"] == text
    assert job["provenance"]["input_changed_during_generation"] is True
    page = client.get(f'/patients/{pids["C"]}/ai-analysis')
    assert text in page.text and "data-aviandr-result" in page.text
    assert "относится к прежнему снимку" in page.text


def test_json_is_readable_without_evidence_codes_and_keeps_raw_transcript(research):
    from app.services.ai_display import readable_text

    raw = json.dumps(
        dict(
            summary="ТЕСТОВАЯ сводка",
            summary_evidence_ids=["F1"],
            hypotheses=[dict(text="ТЕСТОВАЯ гипотеза", evidence_ids=["F2"])],
            questions=[dict(text="Что уточнить?", evidence_ids=["F1"])],
            material_ids=[],
        )
    )
    displayed = readable_text(raw)
    assert "ТЕСТОВАЯ сводка" in displayed and "Что уточнить?" in displayed
    assert "F1" not in displayed and "F2" not in displayed and "\\u" not in displayed
    queue(research, "general", "structured")
    job = run(research, "general", provider("general", raw, []))
    assert job["status"] == "completed"
    if job["result"].get("output_format") == "free_text":
        assert job["result"]["text"] == raw
    page = research[0].get(f'/patients/{research[2]["C"]}/ai-analysis')
    from tests.test_ai_formatting import visible_text

    assert " ".join(displayed.split()) in visible_text(page.text)


def test_unknown_json_fields_are_kept_and_never_executed():
    from app.services.ai_display import readable_text

    value = {"custom": "Дополнительный ответ <script>example()</script>", "number": 7}
    shown = readable_text(json.dumps(value))
    assert json.loads(shown) == value and "Дополнительный ответ" in shown
    assert readable_text('""').strip()
    assert readable_text('{"summary":""}').strip()
