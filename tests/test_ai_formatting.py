"""Model formatting is readable without trusting HTML or changing the transcript."""

import json
from html import unescape
from html.parser import HTMLParser

import httpx
import pytest

from app.experimental_ai.ollama import Ollama
from app.services import aviandr_jobs, aviandr_ollama, patient_ai
from app.services.ai_display import formatted_text
from app.storage import connect, now
from tests.test_diary_library_offline import store, call


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def visible_text(html):
    parser = VisibleText()
    parser.feed(str(html))
    return " ".join(" ".join(parser.parts).split())


def test_markdown_renders_emphasis_lists_headings_quotes_code_and_tables():
    text = """## Сводка
**Жирный текст**, *курсив*, ~~зачёркнуто~~, `код`.

- Первый пункт
- Второй пункт

1. Уточнение
2. Ещё уточнение

> Наблюдение врача

```text
<не HTML>
```

| Показатель | Значение |
| --- | --- |
| Сон | 8 ч |
"""
    html = formatted_text(text)
    for expected in (
        "<h2>Сводка</h2>",
        "<strong>Жирный текст</strong>",
        "<em>курсив</em>",
        "<s>зачёркнуто</s>",
        "<code>код</code>",
        "<ul>",
        "<ol>",
        "<blockquote>",
        "&lt;не HTML&gt;",
        'class="ai-markdown-table"',
        "<td>8 ч</td>",
    ):
        assert expected in html


@pytest.mark.parametrize(
    "text",
    [
        "<script>window.__aiInjected=1</script>",
        '<img src=x onerror="alert(1)">',
        '<svg onload="alert(1)"></svg>',
        "[Переход](javascript:alert(1))",
        "[Переход](data:text/html,test)",
        "![Описание](https://example.invalid/tracking.png)",
    ],
)
def test_model_html_and_active_links_are_inert(text):
    html = str(formatted_text(text))
    assert all(tag not in html for tag in ("<script", "<img", "<svg", "<iframe"))
    assert 'href="javascript:' not in html and 'href="data:' not in html
    assert visible_text(html)


def test_json_keeps_readability_and_unknown_content_without_interpreting_it():
    text = json.dumps(
        dict(
            summary="**Сводка**",
            questions=[dict(text="*Уточнить*", evidence_ids=["F1"])],
        ),
        ensure_ascii=False,
    )
    html = formatted_text(text)
    assert "<strong>Сводка</strong>" in html and "<em>Уточнить</em>" in html
    assert "F1" not in html
    raw = json.dumps(dict(custom="**Исходный текст** <script>test()</script>"))
    shown = str(formatted_text(raw))
    assert shown.startswith("<pre><code>") and "<strong>" not in shown
    content = unescape(shown.removeprefix("<pre><code>").removesuffix("</code></pre>"))
    assert json.loads(content) == json.loads(raw)


@pytest.mark.parametrize("kind", ["general", "aviandr"])
def test_real_worker_response_reaches_formatted_template_and_keeps_raw_text(
    store, kind
):
    _, path, _ = store
    text = "**ТЕХНИЧЕСКАЯ СВОДКА**\n\n- *Наблюдение*\n- Второй пункт"
    with connect(path) as c:
        c.execute(
            "INSERT INTO patient_profiles(patient_id,biography,updated_at) VALUES(1,'Тестовая карта',?)",
            (now(),),
        )
    suffix = "/aviandr" if kind == "aviandr" else ""
    queued = call(
        store,
        f"/patients/{{pid}}/ai-analysis{suffix}/run",
        "POST",
        pid=1,
        form=dict(csrf="direct-unit-csrf", response_mode="free"),
    )
    assert queued.status_code == 303

    def respond(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen2"}}
            )
        return httpx.Response(200, json=dict(done=True, message=dict(content=text)))

    module = aviandr_jobs if kind == "aviandr" else patient_ai
    provider = aviandr_ollama.AviandrOllama if kind == "aviandr" else Ollama
    assert module.process_one(path, provider(httpx.MockTransport(respond)))
    page = call(store, "/patients/{pid}/ai-analysis", pid=1)
    assert "<strong>ТЕХНИЧЕСКАЯ СВОДКА</strong>" in page.body.decode()
    assert "<em>Наблюдение</em>" in page.body.decode()
    job = page.context["av_selected" if kind == "aviandr" else "selected"]
    assert job["result"]["text"] == text and job["status"] == "completed"
    assert job["provenance"]["output_validation"] == "not_checked"


def test_older_structured_responses_also_render_formatted_text(store):
    response = call(store, "/patients/{pid}/ai-analysis", pid=1)
    base = dict(
        id=1,
        status="completed",
        stage="completed",
        created_at="2026-10-27T10:00:00+00:00",
        provenance={},
    )
    general = base | dict(
        result=dict(
            summary="**Сводка**",
            hypotheses=[dict(text="*Гипотеза*")],
            questions=[dict(text="**Вопрос**")],
        )
    )
    explanation = base | dict(
        result=dict(
            summary=[dict(text="**Объяснение**")],
            explanations=[dict(direction="indications", text="*Показания*")],
            questions=[dict(text="**Уточнение**")],
            contradictions=[dict(text="*Пробел*")],
        )
    )
    html = response.template.render(
        response.context | dict(selected=general, av_selected=explanation)
    )
    for fragment in (
        "<strong>Сводка</strong>",
        "<em>Гипотеза</em>",
        "<strong>Вопрос</strong>",
        "<strong>Объяснение</strong>",
        "<em>Показания</em>",
        "<strong>Уточнение</strong>",
        "<em>Пробел</em>",
    ):
        assert fragment in html
