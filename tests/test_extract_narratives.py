"""Declarative documents preserve meaning, unknowns and clinician ownership."""

from copy import deepcopy
import json
import secrets
import pytest
from fastapi import HTTPException
from app.intake_catalog import CATEGORIES, ENTITY_FIELDS
from app.services import (
    extract_narratives as prose,
    patient_extracts as service,
    intake,
)
from app.services.diagnosis_library import search
from app.storage import connect
from tests.test_diary_library_offline import store, call
from tests.test_patient_extracts_direct import create, current, form_for, save, seed


def answered(value):
    return dict(status="answered", value=value)


def field(scope, key, entity=False):
    fields = (
        ENTITY_FIELDS[scope]
        if entity
        else next(c["fields"] for c in CATEGORIES if c["key"] == scope)
    )
    return next(f for f in fields if f["key"] == key)


def render(scope, key, value, **kwargs):
    entity = kwargs.pop("entity", False)
    return prose.field_text(
        scope,
        field(scope, key, entity),
        value,
        context="entity" if entity else "",
        **kwargs
    )


def test_complaints_become_sentences_and_do_not_change_arbitrary_patient_words():
    assert (
        render("complaints", "comment", answered("Беспокойство"))
        == "Пациент жалуется на беспокойство."
    )
    assert (
        render("complaints", "other", answered("Тревога"))
        == "Пациент жалуется на тревогу."
    )
    text = render(
        "complaints",
        "complaint_2",
        answered(
            ["Постоянно о чём-то переживаю", "Трудно контролировать беспокойство"]
        ),
    )
    assert (
        "постоянное беспокойство" in text and "трудности контроля беспокойства" in text
    )
    assert "Что вас" not in text and "переживаю" not in text
    original = "Мне не хочется обсуждать семью; беспокойство отрицаю"
    assert original in render("complaints", "comment", answered(original))


def test_negative_uncertain_declined_and_missing_are_not_interchangeable():
    no = render("medicines", "current", answered("Нет"))
    unknown = render("medicines", "current", dict(status="unknown"))
    declined = render("medicines", "current", dict(status="declined"))
    assert "отрицает текущий приём" in no
    assert "пациент не знает" in unknown and "отрицает" not in unknown
    assert "предпочёл не сообщать" in declined
    assert render("medicines", "current", dict(status="unanswered")) == ""
    assert "требуют уточнения" in render(
        "medicines", "current", answered("Не уверен(а)")
    )
    assert "полнота анамнеза требует проверки" in render(
        "health", "known_conditions", answered("Нет известных заболеваний")
    )


def test_hidden_branches_and_weight_preferences_do_not_enter_narrative():
    category = next(c for c in CATEGORIES if c["key"] == "complaints")
    values = dict(
        groups=answered(["Сон и энергия"]),
        complaint_2=answered(["Постоянно о чём-то переживаю"]),
        complaint_3=answered(["Трудно заснуть"]),
    )
    text = " ".join(prose.fields_text("complaints", category["fields"], values))
    assert "трудности засыпания" in text and "постоянное беспокойство" not in text
    measures = next(c for c in CATEGORIES if c["key"] == "measurements")
    assert not prose.fields_text(
        "measurements",
        measures["fields"],
        dict(weight=answered("68")),
        dict(hide_weight=True),
    )


def test_every_current_field_and_entity_has_declarative_wording_without_questions():
    for entity, scopes in [
        (False, [(c["key"], c["fields"]) for c in CATEGORIES]),
        (True, list(ENTITY_FIELDS.items())),
    ]:
        definitions = prose.ENTITIES if entity else prose.FIELDS
        for scope, fields in scopes:
            assert {f["key"] for f in fields} <= definitions.get(
                scope, {}
            ).keys() | prose.COMMON.keys()
            for f in fields:
                options = f.get("options", [])
                value = (
                    [options[0]]
                    if f["kind"] == "multi"
                    else (
                        options[0]
                        if options
                        else (
                            "2000-01-01"
                            if f["kind"] == "date"
                            else (
                                "5"
                                if f["kind"] in ("number", "scale")
                                else "Сведения пациента"
                            )
                        )
                    )
                )
                text = prose.field_text(
                    scope, f, answered(value), context="entity" if entity else ""
                )
                assert text and "?" not in text, (scope, f["key"], text)
                assert ": «Да»" not in text and ": «Нет»" not in text, (
                    scope,
                    f["key"],
                    text,
                )


def test_symptoms_effects_and_past_medication_stay_reported_rather_than_prescribed():
    text = render("medication", "category", answered("Принимал(а) раньше"), entity=True)
    assert "в прошлом" in text and "сейчас" not in text
    assert "отрицает побочные эффекты" in render(
        "medication", "reactions", answered("Нет"), entity=True
    )
    unknown = render("medication", "reactions", dict(status="unknown"), entity=True)
    assert "отрицает" not in unknown
    assert "причинность не установлена" in render(
        "symptom", "assumption", answered("после таблетки"), entity=True
    )
    assert "действующее вещество" in render(
        "medication", "ingredient", answered("вещество"), entity=True
    )


def test_questionnaire_frequencies_safety_branch_and_zero_keep_original_meaning():
    assert "не отмечалось" in prose.assessment_item(
        "gad7", 1, "Чувство тревоги.", dict(state="answered", value=0)
    )
    assert "почти каждый день" in prose.assessment_item(
        "gad7", 1, "Чувство тревоги.", dict(state="answered", value=3)
    )
    hidden = prose.assessment_item(
        "safety6",
        3,
        "Вы думали о том, как бы вы это сделали?",
        dict(state="not_asked_by_rule", value=None),
    )
    assert "по ветвлению" in hidden and "отрицает" not in hidden and "?" not in hidden
    positive = prose.assessment_item(
        "safety6",
        1,
        "ВОПРОС?",
        dict(state="answered", value=1, comment="важное пояснение", discuss=1),
    )
    assert (
        "желание умереть" in positive
        and "важное пояснение" in positive
        and "обсудить" in positive
        and "ВОПРОС" not in positive
    )


@pytest.mark.parametrize("title", [t[0] for t in service.DOCUMENT_TYPES])
def test_document_type_creates_independent_private_draft_and_repeated_post_is_idempotent(
    store, title
):
    eid = create(store)
    token = secrets.token_urlsafe(24)
    form = dict(csrf="direct-unit-csrf", client_token=token, title=title)
    response = call(
        store, "/patients/{pid}/profile/extracts/new", "POST", pid=1, form=form
    )
    second = int(response.headers["location"].split("/")[-2])
    assert second != eid and current(store, second)["data"]["title"] == title
    assert not current(store, second)["published_revision"]
    assert (
        call(
            store, "/patients/{pid}/profile/extracts/new", "POST", pid=1, form=form
        ).headers["location"]
        == response.headers["location"]
    )
    page = call(store, "/patients/{pid}/profile/extracts/{eid}/edit", pid=1, eid=second)
    assert len(page.context["documents"]) == 2
    assert "Библиотека диагностических формулировок" in page.body.decode()
    assert (
        "data-diagnosis-picker"
        not in call(
            store, "/patients/{pid}/profile/extracts", who="p1", pid=1
        ).body.decode()
    )


def test_library_filters_pick_clear_and_status_are_reviewable_without_javascript(store):
    eid = create(store)
    doc = current(store, eid)
    assert {d["code"] for d in search("ocd", "")} == {"F42.0", "F42.1", "F42.2"}
    assert search("visits", "Z71.1")[0]["code"] == "Z71.1"
    response = save(
        store,
        eid,
        form_for(
            doc,
            intent="diagnosis_search",
            diagnosis_group="ocd",
            diagnosis_query="F42.1",
            conclusion="Сохранить введённый текст",
        ),
    )
    assert response.status_code == 200
    assert response.context["diagnosis_matching"] == {"F42.1"}
    assert (
        response.context["document_data"]["conclusion"] == "Сохранить введённый текст"
    )
    picked = save(
        store,
        eid,
        form_for(
            doc, intent="diagnosis:F42.1", reviewed="yes", diagnosis_status="confirmed"
        ),
    )
    data = picked.context["document_data"]
    assert data["diagnosis"] == "Преимущественно компульсивные действия"
    assert data["diagnosis_status"] == "working" and not data["reviewed"]
    assert (
        current(store, eid)["revision"] == 1
    )  # Search/pick is a preview, not publication.
    picked_form = form_for(
        doc,
        diagnosis_code=data["diagnosis_code"],
        diagnosis=data["diagnosis"],
        diagnosis_status="preliminary",
    )
    assert save(store, eid, picked_form).status_code == 303
    assert current(store, eid)["data"]["diagnosis_status"] == "preliminary"
    cleared = save(store, eid, form_for(current(store, eid), intent="diagnosis_clear"))
    assert not cleared.context["document_data"]["diagnosis"]
    assert current(store, eid)["data"][
        "diagnosis"
    ]  # Preview still needs explicit save.


def test_narrative_in_print_and_patient_version_without_questions_or_private_drafts(
    store,
):
    seed(store)
    eid = create(store)
    form = form_for(
        current(store, eid),
        intent="publish",
        reviewed="yes",
        diagnosis_code="F42.1",
        diagnosis="Преимущественно компульсивные действия",
        diagnosis_status="preliminary",
    )
    assert save(store, eid, form).status_code == 303
    patient = call(
        store, "/patients/{pid}/profile/extracts/{eid}", pid=1, eid=eid, who="p1"
    )
    text = patient.body.decode()
    assert "Пациент описывает жалобы" in text
    assert (
        "Какая причина обращения?" not in text
        and "Комментарий · по желанию:" not in text
    )
    assert "PRIVATE_DRAFT_MARKER" not in text and "Предварительный диагноз" in text
    assert "Библиотека диагностических" not in text
    assert current(store, eid)["data"]["source"]["narrative_version"] == prose.VERSION


def test_refresh_converts_legacy_unchanged_text_keeps_manual_text_and_published_version(
    store,
):
    seed(store)
    eid = create(store)
    _, path, users = store
    doc = current(store, eid)
    data = deepcopy(doc["data"])
    data["source"].pop("narrative_version")
    sections = {s["key"]: s for s in data["source"]["sections"]}
    old = "Какая причина обращения? Беспокойство"
    sections["intake-complaints"].update(original=old, text=old)
    sections["intake-life"].update(text="Ручная формулировка врача")
    data["reviewed"] = True
    with connect(path) as c:
        service.store_version(c, path, users["d1"], 1, eid, doc, data, publish=True)
    published = current(store, eid)["published_revision"]
    assert (
        save(store, eid, form_for(current(store, eid), intent="refresh")).status_code
        == 303
    )
    fresh = current(store, eid)
    sections = {s["key"]: s for s in fresh["data"]["source"]["sections"]}
    assert "Пациент описывает жалобы" in sections["intake-complaints"]["text"]
    assert sections["intake-life"]["text"] == "Ручная формулировка врача"
    assert fresh["published_revision"] == published and not fresh["data"]["reviewed"]
    patient = call(
        store, "/patients/{pid}/profile/extracts/{eid}", pid=1, eid=eid, who="p1"
    )
    assert old in patient.body.decode()
    with connect(path) as c:
        assert (
            intake.read_submission(c, 1)["data"]["answers"]["complaints"]["comment"][
                "value"
            ]
            == "Жалобы из отправленной анкеты"
        )


def test_doctor_assessment_is_human_text_without_opaque_json_or_promoting_stale_review():
    text = prose.doctor_fact(
        "direction_review",
        dict(
            key="contraindications",
            value=dict(decision="complete", snapshot="SECRET-SNAPSHOT"),
            note="Проверены сведения",
        ),
        True,
    )
    assert "противопоказания" in text and "Проверены сведения" in text
    assert "SECRET-SNAPSHOT" not in text and '"decision"' not in text
    assert "актуальность оценивается в анализе ИИ" in text
