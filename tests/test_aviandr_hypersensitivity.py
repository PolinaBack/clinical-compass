import pytest
from app.services import aviandr_applicability as policy
from app.storage import connect
from tests.test_diary_library_offline import store, call
from tests.test_aviandr_colours import share, answer, review, report


@pytest.mark.parametrize(
    "name,kind,expected",
    [
        ("гиперчувствительность", "factor", "unspecified"),
        ("Гиперчувствительность.", "diagnosis", "unspecified"),
        ("повышенная чувствительность", "factor", "unspecified"),
        ("Гиперчувствительность к Авиандру", "factor", "direct"),
        ("Аллергия на маритупирдин", "diagnosis", "direct"),
        ("лактоза", "allergy", "direct"),
        ("Гиперчувствительность к амоксициллину", "factor", None),
        ("Гиперчувствительность к пыльце", "factor", None),
        ("Нет гиперчувствительности", "factor", None),
        ("Гиперчувствительность не выявлена", "factor", None),
    ],
)
def test_hypersensitivity_distinguishes_drug_components_unknown_target_and_unrelated_allergens(
    name, kind, expected
):
    fact = dict(kind=kind, key=name, value=dict(state="present"))
    assert (
        policy.hypersensitivity(
            fact, "Маритупирдин; безводная лактоза; картофельный крахмал"
        )
        == expected
    )
    fact["value"]["state"] = "absent"
    assert policy.hypersensitivity(fact, "Маритупирдин; лактоза") is None


def test_submitted_condition_immediately_changes_refresh_response_to_red_without_model(
    store,
):
    assert (
        review(
            store, "contraindications", "complete", adult_confirmed="yes"
        ).status_code
        == 303
    )
    before = report(store)
    share(
        store,
        entities=[
            dict(
                id=91,
                version=1,
                kind="condition",
                data=dict(name=answer("гиперчувствительность")),
            )
        ],
    )
    response = call(
        store,
        "/api/patients/{pid}/ai-analysis/aviandr/current",
        pid=1,
        checksum=before["checksum"],
        aviandr_id=None,
    )
    import json

    result = json.loads(response.body)
    assert (
        result["changed"]
        and 'id="av-contraindications" data-level="critical"' in result["html"]
    )
    assert "уточните вещество" in result["html"]
    with connect(store[1]) as c:
        assert c.execute("SELECT count(*) FROM aviandr_runs").fetchone()[0] == 0
    assert (
        review(
            store, "contraindications", "complete", adult_confirmed="yes"
        ).status_code
        == 303
    )
    assert (
        next(d for d in report(store)["directions"] if d["key"] == "contraindications")[
            "level"
        ]
        == "critical"
    )
