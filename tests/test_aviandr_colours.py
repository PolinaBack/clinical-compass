"""Current library matches, explicit missing/negative answers and physician review."""

from copy import deepcopy
import json
import secrets

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData
from app.services import aviandr_analysis as analysis, aviandr_data as data
from app.services import aviandr_reviews as reviews, aviandr_applicability as policy
from app.services import intake, drug_library
from app.services import medication_preferences
from app.adverse_catalog import ELIGIBLE
from app.services.aviandr_ollama import input_data
from app.storage import connect
from tests.test_diary_library_offline import store, call


def report(store):
    _, path, users = store
    with connect(path) as c:
        return analysis.build(c, path, users["d1"], 1)


def direction(store, key):
    return next(d for d in report(store)["directions"] if d["key"] == key)


def fact(store, kind, key, state="present", confirmed=True, **values):
    _, path, users = store
    with connect(path) as c:
        return data.add_observation(
            c,
            path,
            users["d1"],
            1,
            kind=kind,
            key=key,
            value=values if kind == "age" else dict(state=state, **values),
            confirmed=confirmed,
            note="Проверено по синтетической карте и опросу пациента.",
        )


def share(store, answers=None, entities=()):
    _, path, users = store
    payload = dict(
        answers=answers or {},
        entities=list(entities),
        preferences={},
        attachments=[],
        links=[],
    )
    with connect(path) as c:
        revision = c.execute(
            "SELECT coalesce(max(revision),0)+1 FROM intake_submissions WHERE patient_id=1"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(1,?,'test',?,?,?)",
            (
                revision,
                json.dumps(payload, ensure_ascii=False),
                users["p1"]["id"],
                "2026-10-27T10:00:00+00:00",
            ),
        )


def answer(value):
    return dict(status="answered", value=value)


def form(store, key, decision, **changes):
    value = dict(
        csrf="direct-unit-csrf",
        direction=key,
        decision=decision,
        snapshot=direction(store, key)["review"]["snapshot"],
        client_token=secrets.token_urlsafe(24),
        review_ack="yes",
        note="Врач проверил данные полного анамнеза и текущую библиотеку.",
    )
    value.update(changes)
    return value


def review(store, key, decision, **changes):
    return call(
        store,
        "/patients/{pid}/ai-analysis/aviandr/review",
        "POST",
        pid=1,
        form=form(store, key, decision, **changes),
    )


def test_empty_card_is_yellow_and_explains_incomplete_disease_history(store):
    value = report(store)
    assert {d["level"] for d in value["directions"]} == {"attention"}
    text = " ".join(value["directions"][1]["narrative"]["conclusion"])
    assert "не предоставил достаточно" in text and "противопоказан" in text
    assert not value["skip_candidate"]


@pytest.mark.parametrize(
    "key,decision",
    [
        ("indications", "gad"),
        ("contraindications", "complete"),
        ("interactions", "confirmed"),
        ("tolerability", "no_effects"),
    ],
)
@pytest.mark.parametrize("note", [None, "", "Да"])
def test_confirmation_needs_no_written_basis_and_immediately_recolours_page(
    store, key, decision, note
):
    fact(store, "age", "age", value=30)
    assert direction(store, key)["level"] == "attention"
    fields = form(store, key, decision)
    if note is None:
        fields.pop("note")
    else:
        fields["note"] = note
    saved = call(
        store, "/patients/{pid}/ai-analysis/aviandr/review", "POST", pid=1, form=fields
    )
    assert saved.status_code == 303
    assert saved.headers["location"].endswith("#av-" + key)
    d = direction(store, key)
    assert d["level"] == "favorable" and d["review"]["current"]
    assert d["review"]["record"]["note"] == (note or "")
    page = call(store, "/patients/{pid}/ai-analysis", pid=1)
    assert f'id="av-{key}" data-level="favorable"' in page.body.decode()
    if not note:
        assert not any("Основание:" in row["text"] for row in d["narrative"]["basis"])


def test_optional_note_does_not_replace_acknowledgement_or_remove_length_limit(store):
    assert (
        review(store, "interactions", "confirmed", note="", review_ack="").status_code
        == 422
    )
    assert (
        review(store, "interactions", "confirmed", note="Я" * 2001).status_code == 422
    )
    assert direction(store, "interactions")["level"] == "attention"


@pytest.mark.parametrize(
    "diagnosis,confirmed,age,expected",
    [
        ("F41.1", True, 30, "favorable"),
        ("F41.1", False, 30, "attention"),
        ("F41.1", True, 17, "critical"),
        ("F32.1", True, 30, "critical"),
        ("F43.2", True, 30, "attention"),
        ("Тревожность", False, 30, "critical"),
    ],
)
def test_indications_require_specific_diagnosis_and_adult_age(
    store, diagnosis, confirmed, age, expected
):
    fact(store, "age", "age", value=age)
    fact(store, "diagnosis", diagnosis, confirmed=confirmed)
    d = direction(store, "indications")
    assert d["level"] == expected
    assert (
        "Генерализованное тревожное расстройство"
        in " ".join(d["narrative"]["conclusion"])
        or "генерализованное" in " ".join(d["narrative"]["conclusion"]).lower()
    )
    assert report(store)["skip_candidate"] == (expected == "critical")


def test_postcovid_review_requires_both_specific_conditions_and_preserves_error_form(
    store,
):
    fact(store, "age", "age", value=30)
    response = review(
        store, "indications", "postcovid", note="Сохранить это основание после ошибки"
    )
    assert (
        response.status_code == 422
        and "Сохранить это основание" in response.body.decode()
    )
    assert direction(store, "indications")["level"] == "attention"
    assert (
        review(
            store, "indications", "postcovid", covid_relation="yes", mild_moderate="yes"
        ).status_code
        == 303
    )
    d = direction(store, "indications")
    assert d["level"] == "favorable" and "COVID-19" in " ".join(
        d["narrative"]["conclusion"]
    )


@pytest.mark.parametrize(
    "kind,key",
    [
        ("factor", "эпилепсия"),
        ("factor", "СКФ <60 мл/мин"),
        ("factor", "печеночная недостаточность"),
        ("factor", "беременность"),
        ("factor", "лактация"),
        ("allergy", "Авиандр"),
    ],
)
def test_explicit_contraindications_are_red_and_full_review_cannot_hide_them(
    store, kind, key
):
    fact(store, "age", "age", value=30)
    fact(store, kind, key, confirmed=False)
    assert direction(store, "contraindications")["level"] == "critical"
    assert review(store, "contraindications", "complete").status_code == 303
    assert direction(store, "contraindications")["level"] == "critical"


def test_complete_history_confirmation_is_snapshot_bound_revocable_and_does_not_publish_drafts(
    store,
):
    fact(store, "age", "age", value=30)
    fields = form(store, "contraindications", "complete")
    for _ in range(2):
        assert (
            call(
                store,
                "/patients/{pid}/ai-analysis/aviandr/review",
                "POST",
                pid=1,
                form=fields,
            ).status_code
            == 303
        )
    assert direction(store, "contraindications")["level"] == "favorable"
    _, path, users = store
    with connect(path) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM aviandr_observations WHERE kind='direction_review'"
            ).fetchone()[0]
            == 1
        )
        draft = intake.draft(c, 1)
        intake.save_category(
            c,
            1,
            users["p1"],
            "health",
            FormData(
                dict(
                    revision=str(draft["revision"]),
                    catalog_version=intake.VERSION,
                    a_description="PRIVATE_UNSENT_DATA",
                    a_known_conditions="Да",
                )
            ),
        )
    assert direction(store, "contraindications")["level"] == "favorable"
    assert "PRIVATE_UNSENT_DATA" not in json.dumps(
        input_data(report(store)), ensure_ascii=False
    )
    fact(store, "factor", "Новое сопутствующее заболевание")
    d = direction(store, "contraindications")
    assert d["review"]["stale"] and d["level"] == "attention"
    stale = call(
        store, "/patients/{pid}/ai-analysis/aviandr/review", "POST", pid=1, form=fields
    )
    assert stale.status_code == 409
    assert review(store, "contraindications", "complete").status_code == 303
    assert direction(store, "contraindications")["level"] == "favorable"
    assert review(store, "contraindications", "revoke").status_code == 303
    assert direction(store, "contraindications")["level"] == "attention"


def test_therapy_empty_and_unknown_require_explicit_clinician_assessment(store):
    assert direction(store, "interactions")["level"] == "attention"
    assert review(store, "interactions", "confirmed").status_code == 303
    assert direction(store, "interactions")["level"] == "favorable"
    share(
        store,
        entities=[
            dict(
                id=42,
                version=1,
                kind="medication",
                data=dict(
                    name=answer("Неизвестное средство"),
                    category=answer("Назначено, принимаю сейчас"),
                ),
            )
        ],
    )
    assert direction(store, "interactions")["level"] == "attention"
    assert review(store, "interactions", "confirmed").status_code == 422
    assert (
        review(store, "interactions", "confirmed", unknown_reviewed="yes").status_code
        == 303
    )
    d = direction(store, "interactions")
    assert d[
        "level"
    ] == "favorable" and "Автоматическая проверка неизвестных" in " ".join(
        d["narrative"]["conclusion"]
    )


def test_full_history_review_does_not_resolve_explicit_unknown_disease(store):
    fact(store, "age", "age", value=30)
    fact(store, "factor", "Почечный фактор", state="unknown")
    assert review(store, "contraindications", "complete").status_code == 303
    assert direction(store, "contraindications")["level"] == "attention"
    fact(store, "factor", "Почечный фактор", state="absent")
    assert direction(store, "contraindications")["review"]["stale"]
    assert review(store, "contraindications", "complete").status_code == 303
    assert direction(store, "contraindications")["level"] == "favorable"


def test_library_warning_is_red_even_after_clinician_confirms_therapy(store):
    _, path, _ = store
    with connect(path) as c:
        med = next(v for v in drug_library.search(c) if v["code"] == "D11")
    fact(
        store,
        "therapy",
        "D11",
        state="current",
        inn=med["inn"],
        identity_confirmed=True,
        start="",
    )
    assert direction(store, "interactions")["level"] == "critical"
    assert review(store, "interactions", "confirmed").status_code == 303
    assert direction(store, "interactions")["level"] == "critical"


def test_no_effects_is_distinct_from_blank_and_matching_effect_is_red(store):
    assert direction(store, "tolerability")["level"] == "attention"
    share(store, answers=dict(medicines=dict(reactions=answer("Нет"))))
    assert direction(store, "tolerability")["level"] == "favorable"
    share(
        store,
        entities=[
            dict(
                id=90,
                version=1,
                kind="reaction",
                data=dict(name=answer("Тошнота"), type=answer("Побочный эффект")),
            )
        ],
    )
    assert direction(store, "tolerability")["level"] == "critical"
    assert review(store, "tolerability", "no_effects").status_code == 303
    assert direction(store, "tolerability")["level"] == "critical"


def test_explicit_clinician_negative_and_nonmatching_uncertain_reactions(store):
    assert review(store, "tolerability", "no_effects").status_code == 303
    assert direction(store, "tolerability")["level"] == "favorable"
    share(
        store,
        entities=[
            dict(
                id=91,
                version=1,
                kind="reaction",
                data=dict(name=answer("Неуточнённое проявление")),
            )
        ],
    )
    assert direction(store, "tolerability")["level"] == "attention"


@pytest.mark.parametrize("selected", ["none", "listed", "not_listed"])
def test_sent_preferences_distinguish_empty_matching_and_nonmatching_answers(
    store, selected
):
    _, path, users = store
    with connect(path) as c:
        registry = policy.inputs(c, report(store), [])["tolerability"]["registry"]
        terms = {(r["system"], r["medical"].casefold()) for r in registry}
        choices = [
            v
            for v in ELIGIBLE.values()
            if ((v["system"], v["medical"].casefold()) in terms)
            == (selected == "listed")
        ]
        code = choices[0]["code"] if selected != "none" else None
        medication_preferences.assign(c, users["d1"], 1)
        fields = [("intent", "send"), ("revision", "0")]
        if code:
            fields += [("effect", code), ("primary", code)]
        medication_preferences.save(c, path, users["p1"], 1, FormData(fields))
    assert direction(store, "tolerability")["level"] == (
        "critical" if selected == "listed" else "favorable"
    )


@pytest.mark.parametrize("role", ["doctor", "patient"])
@pytest.mark.parametrize("drug", ["D01", "D02"])
def test_reported_matching_effect_from_either_author_is_red(store, role, drug):
    with connect(store[1]) as c:
        material = policy.inputs(c, report(store), [])["tolerability"]
    row = next(r for r in material["registry"] if r["medical"] == "тошнота")
    material["reactions"] = [
        dict(code=drug, medical=row["medical"], label=row["label"], author_role=role)
    ]
    assert policy.tolerability(material, dict(current=False))[0] == "critical"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Беременность", "critical"),
        ("Лактация", "critical"),
        ("Возможна беременность", "attention"),
        ("Нет", "favorable"),
    ],
)
def test_reproductive_answers_are_checked_before_a_complete_history_can_be_green(
    store, value, expected
):
    fact(store, "age", "age", value=30)
    share(
        store,
        answers=dict(
            personal=dict(sex=answer("Женский")),
            reproductive=dict(pregnancy=answer(value)),
        ),
    )
    assert review(store, "contraindications", "complete").status_code == 303
    assert direction(store, "contraindications")["level"] == expected


def test_review_snapshot_is_bound_to_patient_even_for_identical_empty_therapy(store):
    fields = form(store, "interactions", "confirmed")
    _, path, _ = store
    with connect(path) as c:
        c.execute("UPDATE patients SET doctor_id=1 WHERE id=2")
    response = call(
        store, "/patients/{pid}/ai-analysis/aviandr/review", "POST", pid=2, form=fields
    )
    assert response.status_code == 409
    with connect(path) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM aviandr_observations WHERE patient_id=2 AND kind='direction_review'"
            ).fetchone()[0]
            == 0
        )


def test_confirmation_access_csrf_and_model_context_preserve_current_decisions(store):
    fields = form(store, "interactions", "confirmed")
    for who, changes in [("p1", {}), ("d2", {}), ("d1", {"csrf": "wrong"})]:
        with pytest.raises(HTTPException) as error:
            call(
                store,
                "/patients/{pid}/ai-analysis/aviandr/review",
                "POST",
                who=who,
                pid=1,
                form=fields | changes,
            )
        assert error.value.status_code == 403
    assert review(store, "interactions", "confirmed").status_code == 303
    value = report(store)
    packet = input_data(value)
    assert (
        next(s for s in packet["statuses"] if s["direction"] == "interactions")["level"]
        == "favorable"
    )
    assert next(
        r for r in packet["clinician_assessments"] if r["direction"] == "interactions"
    )["current"]
    assert packet["library_basis"] and not packet["skip_candidate"]
    assert direction(store, "interactions")["review"]["current"]
    page = call(store, "/patients/{pid}/ai-analysis", pid=1)
    assert page.body.decode().count("data-av-review-form") == 4
