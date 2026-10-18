"""Teaching adaptation: branches and raw facts, never a clinical risk score."""

import pytest
from tests.helpers import login, token
from tests.test_workflows import rows, save_data, start
from app.scales import score
from app.safety import SAFETY_VERSION


def safety_start(client):
    response = client.post("/safety/start", data={"csrf": token(client)})
    assert response.status_code == 200
    aid = int(response.url.path.split("/")[-1])
    assert "Учебная адаптация" in response.text
    return aid


def answer(client, aid, item, value, intent="next", **extras):
    data = save_data(
        client,
        aid,
        [],
        intent=intent,
        step_item=item,
        **{f"answer_{item}": value},
        **extras,
    )
    return client.post(f"/assessments/{aid}/save", data=data)


def values(client, aid):
    return {
        r["item"]: r
        for r in rows(client, "SELECT * FROM answers WHERE assessment_id=?", (aid,))
    }


def test_no_branch_skips_three_questions_but_asks_six_and_has_no_score(client):
    login(client, "patient1")
    aid = safety_start(client)
    assert safety_start(client) == aid
    assert answer(client, aid, 1, 0).url.query == b"item=2&saved=1"
    result = answer(client, aid, 2, 0)
    assert result.url.query == b"item=6&saved=1"
    assert "answer_6" in result.text and "answer_3" not in result.text
    for i in ["3", "4", "5"]:
        assert values(client, aid)[i]["state"] == "not_asked_by_rule"
        assert values(client, aid)[i]["value"] is None
    assert client.get(f"/assessments/{aid}?item=3").url.query == b"item=6"
    assert answer(client, aid, 6, 0, intent="submit").status_code == 200
    result = score("safety6", values(client, aid))
    assert result["complete"] and result["total"] is None and result["count"] == 3
    assert values(client, aid)["7"]["state"] == "not_asked_by_rule"
    assert not rows(client, "SELECT * FROM assessment_events")
    assert rows(client, "SELECT status,version FROM assessments")[0] == {
        "status": "completed",
        "version": SAFETY_VERSION,
    }
    assert (
        "не заключение об отсутствии риска"
        in client.get(f"/assessments/{aid}?review=1").text
    )
    # PHQ-4 follows in a separate assessment window; no safety points are added.
    phq = start(client)
    periods = rows(client, "SELECT session_id,scale FROM assessments ORDER BY id")
    assert periods[0]["session_id"] != periods[1]["session_id"] and phq != aid


def test_yes_branch_and_recent_behavior_have_separate_raw_answers(client):
    login(client, "patient1")
    aid = safety_start(client)
    for item, value in [(1, 0), (2, 1), (3, 0), (4, 0), (5, 0)]:
        assert (
            answer(client, aid, item, value).url.query
            == f"item={item+1}&saved=1".encode()
        )
    result = answer(client, aid, 6, 1)
    assert result.url.query == b"item=7&saved=1"
    assert "За последние 3 месяца" in result.text
    assert answer(client, aid, 7, 0, intent="submit").status_code == 200
    data = values(client, aid)
    assert data["6"]["value"] == 1 and data["7"]["value"] == 0
    result = score("safety6", data)
    assert result["complete"] and result["total"] is None
    assert set(result["positive_items"]) == {"2", "6"}
    events = rows(client, "SELECT kind FROM assessment_events")
    assert len(events) == 2 and all(e["kind"].startswith("safety6_") for e in events)
    login(client, "doctor")
    page = client.get("/patients/1").text
    assert "учебной беседе" in page and "Спасибо, что рассказали" in page
    assert client.get(f"/assessments/{aid}").status_code == 200


@pytest.mark.parametrize("missing", ["declined", "deferred", "unanswered"])
def test_unknown_second_answer_never_becomes_the_negative_branch(client, missing):
    login(client, "patient1")
    aid = safety_start(client)
    answer(client, aid, 1, 0)
    assert answer(client, aid, 2, missing).url.query == b"item=6&saved=1"
    assert answer(client, aid, 6, 0, intent="submit").status_code == 200
    data = values(client, aid)
    assert all(data[str(i)]["state"] == "unanswered" for i in [3, 4, 5])
    assert data["2"]["value"] is None and data["2"]["state"] == missing
    assert not score("safety6", data)["complete"]
    assert rows(client, "SELECT status FROM assessments")[0]["status"] == "incomplete"


def test_branch_change_preserves_history_narrative_events_and_is_idempotent(client):
    login(client, "patient1")
    aid = safety_start(client)
    answer(client, aid, 1, 0)
    answer(client, aid, 2, 1)
    answer(client, aid, 3, 1, comment_3="Синтетическое пояснение", discuss_3="true")
    previous_events = len(rows(client, "SELECT * FROM assessment_events"))
    assert answer(client, aid, 2, 0).status_code == 200
    third = values(client, aid)["3"]
    assert third["value"] is None and third["state"] == "not_asked_by_rule"
    assert third["comment"] == "Синтетическое пояснение" and third["discuss"] == 1
    assert rows(
        client,
        "SELECT * FROM answer_history WHERE payload LIKE '%Синтетическое пояснение%'",
    )
    assert len(rows(client, "SELECT * FROM assessment_events")) == previous_events
    assert answer(client, aid, 2, 1).status_code == 200
    assert values(client, aid)["3"]["state"] == "unanswered"
    data = save_data(client, aid, [], intent="draft", step_item=3, answer_3=1)
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    count = len(rows(client, "SELECT * FROM assessment_events"))
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    assert len(rows(client, "SELECT * FROM assessment_events")) == count


def test_safety_audio_comments_and_roles_are_scoped(client):
    login(client, "patient1")
    aid = safety_start(client)
    assert (
        answer(
            client,
            aid,
            1,
            0,
            intent="draft",
            comment_1="Учебная история",
            discuss_1="true",
        ).status_code
        == 200
    )
    response = client.post(
        f"/assessments/{aid}/audio",
        data={"csrf": token(client), "item": "1", "duration": "1", "consent": "true"},
        files={"recording": ("demo.webm", b"\x1aE\xdf\xa3synthetic", "audio/webm")},
    )
    assert response.status_code == 200
    assert values(client, aid)["1"]["value"] == 0
    login(client, "patient2")
    assert client.get(f"/assessments/{aid}").status_code == 403
    assert client.get("/audio/1").status_code == 403
    login(client, "doctor")
    assert client.get("/audio/1").status_code == 200
    assert client.post("/safety/start", data={"csrf": token(client)}).status_code == 403


def test_cannot_submit_structurally_hidden_items_or_nonbinary_answer(client):
    login(client, "patient1")
    aid = safety_start(client)
    answer(client, aid, 1, 0)
    answer(client, aid, 2, 0)
    assert answer(client, aid, 3, 1).status_code == 409
    assert answer(client, aid, 6, 3, intent="submit").status_code == 422
    assert answer(client, aid, 6, 1).status_code == 200
    assert answer(client, aid, 7, "declined", intent="submit").status_code == 200
    assert not score("safety6", values(client, aid))["complete"]


def test_changing_sixth_answer_on_last_screen_continues_without_javascript(client):
    login(client, "patient1")
    aid = safety_start(client)
    answer(client, aid, 1, 0)
    answer(client, aid, 2, 0)
    # The rendered page initially has a finish button. A new yes needs Q6a.
    result = answer(client, aid, 6, 1, intent="submit")
    assert result.status_code == 200 and result.url.query == b"item=7&saved=1"
    assert rows(client, "SELECT status FROM assessments")[0]["status"] == "draft"
    assert answer(client, aid, 7, 0, intent="submit").status_code == 200
    assert score("safety6", values(client, aid))["complete"]
