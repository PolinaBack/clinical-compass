"""Semantic matches use actual local HTTP payloads, immutable source snapshots
and real clinician confirmation routes. Models here are transport fixtures.
"""

from copy import deepcopy
import json
import httpx
import pytest
from starlette.requests import Request
from urllib.parse import urlencode

from app.experimental_ai.scenarios import digest
from app.services import aviandr_jobs, aviandr_ollama, aviandr_matching as matching
from app.storage import connect
from tests.test_diary_library_offline import store, call, immediate
from tests.test_aviandr_colours import fact, direction, report, review, form


def model(
    store,
    key,
    state,
    quote,
    library_quote=None,
    indication="",
    malformed=False,
    final_failure=None,
):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json=dict(model_info={"general.architecture": "qwen2"})
            )
        payload = json.loads(request.content)
        packet = json.loads(payload["messages"][1]["content"])
        if "comparison_material" in packet:
            material = packet["comparison_material"][key]
            content = (
                "not JSON"
                if malformed
                else json.dumps(
                    dict(
                        comparisons=[
                            dict(
                                direction=key,
                                assessment=state,
                                explanation="**Сопоставление**: " + quote,
                                patient_quote=quote,
                                library_quote=library_quote or material["library"],
                                indication=indication,
                            )
                        ]
                    ),
                    ensure_ascii=False,
                )
            )
        else:
            if final_failure == "connection":
                raise httpx.ConnectError("synthetic offline", request=request)
            if final_failure == "empty":
                return httpx.Response(
                    200, json=dict(done=True, message=dict(content=""))
                )
            content = "**Объяснение модели**, полученное после сопоставления."
        return httpx.Response(200, json=dict(done=True, message=dict(content=content)))

    with connect(store[1]) as c:
        ident = aviandr_jobs.enqueue(
            c, store[1], store[2]["d1"], 1, response_mode="free"
        )
    assert (
        aviandr_jobs.process_one(
            store[1], aviandr_ollama.AviandrOllama(httpx.MockTransport(respond))
        )
        == ident
    )
    with connect(store[1]) as c:
        job = aviandr_jobs.decoded(
            c,
            store[1],
            c.execute("SELECT * FROM aviandr_runs WHERE id=?", (ident,)).fetchone(),
        )
    assert job["status"] == "completed", job["error_code"]
    return job, calls


def test_freely_worded_indication_is_compared_by_model_then_confirmed_without_regeneration(
    store,
):
    fact(store, "age", "age", value=30)
    fact(store, "diagnosis", "Генерализованное тревожное расстройство (ГТР)")
    assert direction(store, "indications")["level"] == "critical"  # no exact alias
    job, calls = model(
        store,
        "indications",
        "match",
        "Генерализованное тревожное расстройство (ГТР)",
        indication="gad",
    )
    d = direction(store, "indications")
    assert d["level"] == "attention" and d["ai_comparison"]["assessment"] == "match"
    assert not call(store, "/patients/{pid}/ai-analysis", pid=1).context["av_stale"]
    assert review(store, "indications", "gad", note="").status_code == 303
    assert direction(store, "indications")["level"] == "favorable"
    assert len(calls) == 3  # model metadata, semantic matching, explanation
    explanation_packet = json.loads(
        json.loads(calls[-1].content)["messages"][1]["content"]
    )
    assert explanation_packet["statuses"][0]["level"] == "attention"
    html = call(store, "/patients/{pid}/ai-analysis", pid=1).body.decode()
    assert (
        "<strong>Сопоставление</strong>" in html
        and "<strong>Объяснение модели</strong>" in html
    )
    assert 'id="av-indications" data-level="favorable"' in html
    assert job["result"]["clinical_comparison"]["indications"] == d["ai_comparison"]


def test_model_discovers_description_of_a_contraindication_and_confirmation_keeps_it_red(
    store,
):
    fact(store, "age", "age", value=30)
    fact(store, "factor", "Эпилепсия с редкими приступами")
    assert direction(store, "contraindications")["level"] == "attention"
    model(
        store,
        "contraindications",
        "match",
        "Эпилепсия с редкими приступами",
        "эпилепсия",
    )
    assert direction(store, "contraindications")["level"] == "critical"
    assert review(store, "contraindications", "complete", note="").status_code == 303
    assert direction(store, "contraindications")["level"] == "critical"


def test_no_match_keeps_yellow_until_confirmation_then_green_and_becomes_stale_on_edit(
    store,
):
    fact(store, "age", "age", value=30)
    fact(store, "factor", "Сезонный аллергический ринит")
    model(store, "contraindications", "no_match", "Сезонный аллергический ринит")
    assert direction(store, "contraindications")["level"] == "attention"
    assert review(store, "contraindications", "complete", note="").status_code == 303
    assert direction(store, "contraindications")["level"] == "favorable"
    fact(store, "factor", "Другое заболевание")
    d = direction(store, "contraindications")
    assert (
        d["ai_comparison"] is None
        and d["review"]["stale"]
        and d["level"] == "attention"
    )


def test_model_cannot_turn_a_known_restriction_green(store):
    fact(store, "age", "age", value=30)
    fact(store, "factor", "эпилепсия")
    model(store, "contraindications", "no_match", "эпилепсия")
    assert review(store, "contraindications", "complete", note="").status_code == 303
    assert direction(store, "contraindications")["level"] == "critical"


@pytest.mark.parametrize(
    "bad_quote", ["Выдуманная болезнь", "Запись врача", "авиандр-colours-v4-semantic"]
)
def test_unknown_source_or_administrative_quote_cannot_create_a_red_match(
    store, bad_quote
):
    fact(store, "age", "age", value=30)
    job, _ = model(store, "contraindications", "match", bad_quote, "эпилепсия")
    assert (
        job["result"]["clinical_comparison"]["contraindications"]["assessment"]
        == "uncertain"
    )
    assert direction(store, "contraindications")["level"] == "attention"


def test_malformed_comparison_does_not_suppress_free_answer(store):
    job, _ = model(store, "contraindications", "match", "anything", malformed=True)
    assert "clinical_comparison" not in job["result"]
    assert job["result"]["text"].startswith("**Объяснение модели**")


def test_negative_structured_fact_and_missing_library_quote_are_not_risks(store):
    material = deepcopy(report(store)["comparison_material"])
    material["contraindications"]["facts"] = [
        dict(kind="factor", key="Эпилепсия", value=dict(state="absent"))
    ]
    row = dict(
        direction="contraindications",
        assessment="match",
        explanation="Отрицательный ответ",
        patient_quote="Эпилепсия",
        library_quote="эпилепсия",
        indication="",
    )
    envelope = dict(
        done=True, message=dict(content=json.dumps(dict(comparisons=[row])))
    )
    assert (
        matching.read(envelope, material)["contraindications"]["assessment"]
        == "uncertain"
    )
    row["library_quote"] = "Выдуманное ограничение"
    envelope["message"]["content"] = json.dumps(dict(comparisons=[row]))
    assert (
        matching.read(envelope, material)["contraindications"]["assessment"]
        == "uncertain"
    )


def test_new_model_assessment_invalidates_previously_saved_confirmation(store):
    fact(store, "age", "age", value=30)
    assert review(store, "contraindications", "complete", note="").status_code == 303
    assert direction(store, "contraindications")["level"] == "favorable"
    model(store, "contraindications", "uncertain", "Данных недостаточно")
    d = direction(store, "contraindications")
    assert d["review"]["stale"] and d["level"] == "attention"
    assert review(store, "contraindications", "complete", note="").status_code == 303
    assert direction(store, "contraindications")["level"] == "favorable"


def test_comparison_is_bound_to_the_patient_id(store):
    fact(store, "age", "age", value=30)
    job, _ = model(store, "contraindications", "uncertain", "Данных недостаточно")
    with connect(store[1]) as c:
        c.execute("UPDATE patients SET doctor_id=1 WHERE id=2")
        materials = deepcopy(report(store)["comparison_material"])
        materials["contraindications"]["patient_id"] = 2
        assert not matching.load(c, store[1], 2, materials)
    assert job["result"]["clinical_comparison"]["contraindications"][
        "input_hash"
    ] != digest(materials["contraindications"])


@pytest.mark.parametrize("failure", ["empty", "connection"])
def test_genuine_comparison_text_survives_failure_of_the_second_model_pass(
    store, failure
):
    job, _ = model(
        store,
        "contraindications",
        "uncertain",
        "Нужно уточнить заболевания",
        final_failure=failure,
    )
    assert job["result"]["text"] == "**Сопоставление**: Нужно уточнить заболевания"
    assert job["provenance"]["explanation_error"]
    assert direction(store, "contraindications")["ai_comparison"]


def json_review(store, fields):
    app, _, users = store
    user = users["d1"]
    body = urlencode(fields).encode()

    async def receive():
        return dict(type="http.request", body=body, more_body=False)

    request = Request(
        dict(
            type="http",
            method="POST",
            scheme="http",
            path="/patients/1/ai-analysis/aviandr/review",
            query_string=b"",
            headers=[
                (b"host", b"unit.test"),
                (b"accept", b"application/json"),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
            server=("unit.test", 80),
            client=("127.0.0.1", 1000),
            app=app,
            session=dict(
                account_id=user["id"],
                auth_version=user["auth_version"],
                user="doctor",
                csrf="direct-unit-csrf",
                patient_id=None,
            ),
        ),
        receive,
    )
    route = next(
        r
        for r in app.routes
        if getattr(r, "path", "") == "/patients/{pid}/ai-analysis/aviandr/review"
    )
    return immediate(route.endpoint(request, pid=1))


def test_ajax_confirmation_returns_recoloured_fragment_without_redirect_and_is_idempotent(
    store,
):
    fact(store, "age", "age", value=30)
    fields = form(store, "contraindications", "complete", note="")
    for _ in range(2):
        response = json_review(store, fields)
        assert response.status_code == 200
        payload = json.loads(response.body)
        assert payload["changed"]
        assert 'id="av-contraindications" data-level="favorable"' in payload["html"]
        assert "Анамнез проверен врачом" in payload["html"]
    with connect(store[1]) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM aviandr_observations WHERE kind='direction_review'"
            ).fetchone()[0]
            == 1
        )


def test_ajax_error_keeps_original_form_and_does_not_confirm_changed_data(store):
    fields = form(
        store, "contraindications", "complete", note="Несохранённое основание"
    )
    fact(store, "age", "age", value=30)
    response = json_review(store, fields)
    assert response.status_code == 409
    html = response.body.decode()
    assert (
        "Несохранённое основание" in html
        and 'id="av-review-contraindications" open' in html
    )
    assert not direction(store, "contraindications")["review"]["current"]


@pytest.mark.parametrize("state", [None, "absent", "unknown", "present"])
def test_model_cannot_call_missing_or_negative_diagnosis_a_mismatching_indication(
    state,
):
    material = dict(
        facts=(
            []
            if state is None
            else [dict(kind="diagnosis", key="F43.2", value=dict(state=state))]
        )
    )
    value = matching.evaluate(
        "indications",
        material,
        dict(current=False),
        ("attention", "Нужно уточнить показание", "Недостаточно данных", ""),
        dict(assessment="no_match"),
    )
    assert value[0] == ("critical" if state == "present" else "attention")
