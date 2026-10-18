import pytest
from app.scales import score, combined, cssrs_branch, SCALES


def answers(values):
    return {
        str(i): {
            "value": value,
            "state": "answered" if value is not None else "unanswered",
        }
        for i, value in enumerate(values, 1)
    }


@pytest.mark.parametrize(
    "scale,count,maximum,total",
    [("phq4", 4, 3, 12), ("phq9", 9, 3, 27), ("gad7", 7, 3, 21), ("hcl33", 33, 1, 33)],
)
def test_complete_extremes(scale, count, maximum, total):
    assert score(scale, answers([0] * count))["total"] == 0
    assert score(scale, answers([maximum] * count))["total"] == total


@pytest.mark.parametrize(
    "state", ["unanswered", "deferred", "declined", "not_asked_by_rule"]
)
def test_absence_is_never_zero(state):
    data = answers([0] * 9)
    data["2"] = {"value": None, "state": state}
    result = score("phq9", data)
    assert result["total"] is None and result["answered"] == 8


@pytest.mark.parametrize(
    "values,phq2,gad2",
    [
        ([1, 1, 1, 1], 2, 2),
        ([1, 2, 1, 1], 2, 3),
        ([1, 1, 1, 2], 3, 2),
        ([None, 0, 2, 1], 3, None),
    ],
)
def test_phq4_subscales_do_not_use_overall_sum(values, phq2, gad2):
    result = score("phq4", answers(values))
    assert result["phq2"] == phq2 and result["gad2"] == gad2


def test_phq9_item9_independent_of_total_and_hcl_context_excluded():
    result = score("phq9", answers([0] * 8 + [1]))
    assert result["total"] == 1 and result["needs_safety_clarification"]
    data = answers([1] * 33)
    data["ctx_days"] = {"value": 366, "state": "answered"}
    assert score("hcl33", data)["total"] == 33


@pytest.mark.parametrize(
    "field", ["session_id", "version", "period_start", "period_end"]
)
def test_phq_ads_requires_compatible_period_and_session(field):
    a = {
        "session_id": 1,
        "version": "ru",
        "period_start": "2026-10-01",
        "period_end": "2026-10-14",
        "total": 5,
    }
    b = {**a, "total": 7}
    assert combined(a, b)[0] == 12
    b[field] = "different"
    assert combined(a, b)[0] is None


def test_phq_ads_requires_both_complete_forms():
    a = {
        "session_id": 1,
        "version": "ru",
        "period_start": "2026-10-01",
        "period_end": "2026-10-14",
        "total": None,
    }
    assert combined(a, {**a, "total": 2})[0] is None
    assert combined(a, None)[0] is None


def test_cssrs_branch_does_not_fabricate_negative_answers():
    result = cssrs_branch("no")
    assert all(result[str(i)] == "not_asked_by_rule" for i in (3, 4, 5))
    assert result["6"] == "unanswered"
    assert cssrs_branch(None)["3"] == "unanswered"


@pytest.mark.parametrize(
    "scale,total,expected",
    [
        ("phq9", 4, "Минимальные симптомы"),
        ("phq9", 5, "Лёгкие симптомы"),
        ("phq9", 10, "Умеренные симптомы"),
        ("phq9", 15, "Умеренно тяжёлые симптомы"),
        ("phq9", 20, "Тяжёлые симптомы"),
        ("gad7", 15, "Тяжёлые симптомы"),
        ("gad7", None, None),
        ("hcl33", 33, None),
    ],
)
def test_symptom_descriptions_have_no_diagnostic_or_hcl_threshold(
    scale, total, expected
):
    from app.scales import symptom_category

    assert symptom_category(scale, total) == expected
