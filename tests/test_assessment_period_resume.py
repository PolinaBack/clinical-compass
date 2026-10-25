"""Synthetic questionnaires spanning days: real persistence, original dates and scope."""

import secrets
from datetime import date, timedelta

import pytest
from app.storage import connect, now
from app.scales import VERSION, SCALES
from app.safety import SAFETY_VERSION
from tests.helpers import login, token
from tests.test_workflows import save_data, rows
from tests.test_safety_adaptation import answer as safety_answer


def older_assessment(client, scale="phq4", days=1):
    end = date.today() - timedelta(days=days)
    start = end - timedelta(days=29 if scale == "safety6" else 13)
    with connect(client.app.state.db_path) as c:
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
            (1, start.isoformat(), end.isoformat(), now()),
        ).lastrowid
        aid = c.execute(
            "INSERT INTO assessments(session_id,patient_id,scale,version) VALUES(?,?,?,?)",
            (sid, 1, scale, SAFETY_VERSION if scale == "safety6" else VERSION),
        ).lastrowid
    return aid, sid, (start.isoformat(), end.isoformat())


def step(client, aid, item, value, intent="next", **extra):
    data = save_data(
        client,
        aid,
        [],
        intent=intent,
        step_item=item,
        **{f"answer_{item}": value},
        **extra,
    )
    return client.post(f"/assessments/{aid}/save", data=data)


def test_reproduces_inaccessible_period_confirmation_on_next_day(client):
    login(client, "patient1")
    aid, sid, period = older_assessment(client)
    data = save_data(
        client,
        aid,
        [],
        intent="next",
        step_item=1,
        answer_1="2",
        comment_1="Синтетический комментарий",
    )
    assert "period_ack" not in data
    response = client.post(f"/assessments/{aid}/save", data=data)
    assert response.status_code == 200
    assert response.url.query == b"item=2&saved=1"
    assert rows(
        client,
        "SELECT value,comment FROM answers WHERE assessment_id=? AND item=?",
        (aid, "1"),
    ) == [dict(value=2, comment="Синтетический комментарий")]
    assert rows(
        client,
        "SELECT period_start,period_end FROM assessment_sessions WHERE id=?",
        (sid,),
    )[0] == dict(zip(("period_start", "period_end"), period))


@pytest.mark.parametrize("scale", list(SCALES))
def test_all_existing_scales_can_pause_and_resume_after_their_original_period(
    client, scale
):
    login(client, "patient1")
    aid, sid, period = older_assessment(client, scale, days=30)
    page = client.get(f"/assessments/{aid}")
    assert page.status_code == 200
    assert ("data-assessment-period-note" in page.text) == (scale != "hcl33")
    response = step(client, aid, 1, "0", "draft")
    assert response.status_code == 200
    page = client.get(f"/assessments/{aid}?item=1")
    assert page.context["answers"]["1"]["value"] == 0
    assert step(client, aid, 1, "0").status_code == 200
    assert rows(
        client,
        "SELECT period_start,period_end FROM assessment_sessions WHERE id=?",
        (sid,),
    )[0] == dict(zip(("period_start", "period_end"), period))


@pytest.mark.parametrize("intent", ["draft", "next", "back", "submit"])
def test_old_period_does_not_block_navigation_or_submission(client, intent):
    login(client, "patient1")
    aid, _, _ = older_assessment(client)
    assert step(client, aid, 1, "2", comment_1="Первый ответ").status_code == 200
    assert step(client, aid, 2, "0", intent).status_code == 200
    answers = rows(
        client,
        "SELECT item,value,comment FROM answers WHERE assessment_id=? ORDER BY item",
        (aid,),
    )
    assert answers[:2] == [
        dict(item="1", value=2, comment="Первый ответ"),
        dict(item="2", value=0, comment=""),
    ]


def test_crossing_midnight_after_page_load_does_not_reject_entered_answer(
    client, monkeypatch
):
    login(client, "patient1")
    aid, _, _ = older_assessment(client, days=0)
    page = client.get(f"/assessments/{aid}")
    assert "data-assessment-period-note" not in page.text
    data = save_data(client, aid, [], intent="next", step_item=1, answer_1="1")
    current = date.today()

    class Tomorrow(date):
        @classmethod
        def today(cls):
            return current + timedelta(days=1)

    monkeypatch.setattr("app.workflows.date", Tomorrow)
    response = client.post(f"/assessments/{aid}/save", data=data)
    assert (
        response.status_code == 200 and "data-assessment-period-note" in response.text
    )
    assert response.context["answers"]["1"]["value"] == 1


def test_old_phq4_and_automatic_followups_finish_in_same_original_session(client):
    login(client, "patient1")
    aid, sid, period = older_assessment(client)
    for i, value in enumerate([2, 1, 2, 1], 1):
        assert (
            step(client, aid, i, str(value), "submit" if i == 4 else "next").status_code
            == 200
        )
    assessments = rows(
        client, "SELECT * FROM assessments WHERE session_id=? ORDER BY id", (sid,)
    )
    assert {a["scale"] for a in assessments} == {"phq4", "phq9", "gad7"}
    assert assessments[0]["status"] == "completed"
    for a in assessments[1:]:
        for i in range(1, len(SCALES[a["scale"]]["items"]) + 1):
            assert (
                step(
                    client,
                    a["id"],
                    i,
                    "1",
                    "submit" if i == len(SCALES[a["scale"]]["items"]) else "next",
                ).status_code
                == 200
            )
    assert all(
        a["status"] == "completed"
        for a in rows(
            client, "SELECT status FROM assessments WHERE session_id=?", (sid,)
        )
    )
    assert rows(
        client,
        "SELECT period_start,period_end FROM assessment_sessions WHERE id=?",
        (sid,),
    )[0] == dict(zip(("period_start", "period_end"), period))
    assert (
        len(
            rows(
                client, "SELECT * FROM assessment_events WHERE kind=?", ("phq9_item9",)
            )
        )
        == 1
    )
    login(client, "doctor")
    for a in assessments:
        page = client.get(f'/assessments/{a["id"]}')
        assert (
            page.status_code == 200
            and page.context["assessment"]["period_end"] == period[1]
        )


def test_old_safety_branch_completes_without_confirmation_or_fabricated_values(client):
    login(client, "patient1")
    aid, _, _ = older_assessment(client, "safety6")
    assert safety_answer(client, aid, 1, 0).status_code == 200
    assert safety_answer(client, aid, 2, 0).url.query == b"item=6&saved=1"
    assert safety_answer(client, aid, 6, 1).url.query == b"item=7&saved=1"
    assert safety_answer(client, aid, 7, 0, intent="submit").status_code == 200
    answer_rows = rows(
        client, "SELECT item,value,state FROM answers WHERE assessment_id=?", (aid,)
    )
    assert all(
        r["value"] is None and r["state"] == "not_asked_by_rule"
        for r in answer_rows
        if r["item"] in ("3", "4", "5")
    )
    assert (
        rows(client, "SELECT status FROM assessments WHERE id=?", (aid,))[0]["status"]
        == "completed"
    )


def test_actual_invalid_answers_conflicts_and_permissions_still_preserve_saved_data(
    client,
):
    login(client, "patient1")
    aid, _, _ = older_assessment(client)
    data = save_data(client, aid, [], intent="next", step_item=1, answer_1="2")
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    assert client.post(f"/assessments/{aid}/save", data=data).status_code == 200
    stale = dict(data, request_id=secrets.token_urlsafe(24), answer_1="0")
    assert client.post(f"/assessments/{aid}/save", data=stale).status_code == 409
    bad = save_data(client, aid, [], intent="next", step_item=2, answer_2="9")
    assert client.post(f"/assessments/{aid}/save", data=bad).status_code == 422
    assert rows(
        client, "SELECT value FROM answers WHERE assessment_id=? AND item=?", (aid, "1")
    ) == [dict(value=2)]
    bad["answer_2"] = "0"
    bad["csrf"] = "bad"
    assert client.post(f"/assessments/{aid}/save", data=bad).status_code == 403
    login(client, "patient2")
    assert (
        client.post(
            f"/assessments/{aid}/save", data={"csrf": token(client)}
        ).status_code
        == 403
    )
    login(client, "doctor")
    assert (
        client.post(
            f"/assessments/{aid}/save", data={"csrf": token(client)}
        ).status_code
        == 403
    )
