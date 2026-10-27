"""Reproduce a saved review on an empty chart, rather than seeding age first."""

import pytest
from app.storage import connect
from app.services.aviandr_applicability import age_state
from tests.test_diary_library_offline import store, call
from tests.test_aviandr_colours import (
    report,
    direction,
    review,
    fact,
    form,
    share,
    answer,
)


def test_saved_history_confirmation_explains_only_the_remaining_age_gap(store):
    assert review(store, "contraindications", "complete", note="").status_code == 303
    d = direction(store, "contraindications")
    assert d["review"]["current"] and d["review"]["age_missing"]
    assert d["level"] == "attention"
    assert "Анамнез подтверждён" in d["status"]
    assert "Возраст не указан" in d["narrative"]["unknowns"][0]
    assert "Пациент не предоставил" not in " ".join(d["narrative"]["conclusion"])
    assert "аллергологический анамнез" not in str(d["narrative"]["questions"])
    html = call(store, "/patients/{pid}/ai-analysis", pid=1).body.decode()
    assert 'name="adult_confirmed"' in html


@pytest.mark.parametrize(
    "key,decision", [("contraindications", "complete"), ("indications", "gad")]
)
def test_inline_adult_confirmation_makes_suitable_empty_chart_green_without_fake_age(
    store, key, decision
):
    assert (
        review(store, key, decision, adult_confirmed="yes", note="").status_code == 303
    )
    d = direction(store, key)
    assert d["level"] == "favorable" and d["review"]["adult_confirmed"]
    assert not any(f["kind"] == "age" for f in report(store)["data"]["facts"])
    assert report(store)["data"]["demographics"] == {}
    html = call(store, "/patients/{pid}/ai-analysis", pid=1).body.decode()
    assert 'name="adult_confirmed" value="yes" checked' in html
    if key == "contraindications":
        assert "Возраст не уточнён" not in " ".join(d["narrative"]["conclusion"])
        assert "18+ подтверждено врачом" in str(d["narrative"]["basis"])


@pytest.mark.parametrize(
    "key,decision", [("contraindications", "complete"), ("indications", "gad")]
)
def test_adult_checkbox_cannot_override_recorded_minor_age(store, key, decision):
    fact(store, "age", "age", value=17)
    assert review(store, key, decision, adult_confirmed="yes").status_code == 422
    assert direction(store, key)["level"] == "critical"


def test_conflicting_age_cannot_be_overridden_with_adult_checkbox(store):
    fact(store, "age", "age", value=30)
    share(store, answers=dict(personal=dict(birth_date=answer("2009-01-01"))))
    assert age_state(report(store)["data"]["facts"]) == "unknown"
    assert (
        review(
            store, "contraindications", "complete", adult_confirmed="yes"
        ).status_code
        == 422
    )
    assert direction(store, "contraindications")["level"] != "favorable"


def test_adult_confirmation_does_not_hide_known_risk_or_unknown_factor(store):
    fact(store, "factor", "Почечный фактор", state="unknown")
    assert (
        review(
            store, "contraindications", "complete", adult_confirmed="yes"
        ).status_code
        == 303
    )
    assert direction(store, "contraindications")["level"] == "attention"
    fact(store, "factor", "эпилепсия")
    assert (
        review(
            store, "contraindications", "complete", adult_confirmed="yes"
        ).status_code
        == 303
    )
    assert direction(store, "contraindications")["level"] == "critical"


def test_adult_confirmation_remains_idempotent_and_is_invalidated_by_new_data(store):
    fields = form(
        store, "contraindications", "complete", adult_confirmed="yes", note=""
    )
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
    with connect(store[1]) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM aviandr_observations WHERE kind='direction_review'"
            ).fetchone()[0]
            == 1
        )
    fact(store, "factor", "Новое заболевание")
    d = direction(store, "contraindications")
    assert d["review"]["stale"] and not d["review"]["adult_confirmed"]
    assert d["level"] == "attention"


def test_real_submitted_birth_date_needs_no_extra_age_checkbox(store):
    share(store, answers=dict(personal=dict(birth_date=answer("1994-06-11"))))
    assert review(store, "contraindications", "complete", note="").status_code == 303
    d = direction(store, "contraindications")
    assert d["level"] == "favorable" and not d["review"]["age_missing"]


@pytest.mark.parametrize("key", ["indications", "interactions"])
def test_saved_fields_and_confirmation_survive_reopening_without_new_writes(store, key):
    import re
    if key == "indications":
        decision = "postcovid"
        fields = dict(adult_confirmed="yes", covid_relation="yes", mild_moderate="yes")
        flags = ["adult_confirmed", "covid_relation", "mild_moderate", "review_ack"]
    else:
        share(store, entities=[dict(id=3, version=1, kind="medication", data=dict(name=answer("Неизвестное средство"), category=answer("Назначено, принимаю сейчас")))])
        decision, fields, flags = "confirmed", dict(unknown_reviewed="yes"), ["unknown_reviewed", "review_ack"]
    note = "Врач проверил по документам и беседе."
    assert review(store, key, decision, note=note, **fields).status_code == 303
    before = direction(store, key)["review"]["snapshot"]
    for _ in range(3):
        page = call(store, "/patients/{pid}/ai-analysis", pid=1)
        d = next(d for d in page.context["av_report"]["directions"] if d["key"] == key)
        assert d["review"]["current"] and d["review"]["snapshot"] == before
        assert d["level"] == "favorable"
        article = page.body.decode().split(f'id="av-{key}"',1)[1].split('</article>',1)[0]
        for flag in flags:
            assert re.search(r'name="'+flag+r'"[^>]*checked', article)
        assert re.search(r'<textarea[^>]*name="note"[^>]*>'+re.escape(note)+r'</textarea>', article)
        assert 'value="'+decision+'" selected' in article
        assert "Подтверждение сохранено · изменить" in article
    with connect(store[1]) as c:
        assert c.execute("SELECT count(*) FROM aviandr_observations WHERE kind='direction_review'").fetchone()[0] == 1
