import json
import secrets
from datetime import date
import pytest
from fastapi import HTTPException
from app.safety import SAFETY_VERSION, safety_result, safety_urgent_items
from app.storage import connect
from app.security import verify_password
from app.services import safety_alerts
from scripts.create_safety_test_account import create_patient
from tests.test_diary_library_offline import store, call


def raw(values):
    return {
        str(i): dict(
            value=v if type(v) is int else None,
            state="answered" if type(v) is int else v,
            comment="",
            discuss=0,
        )
        for i, v in values.items()
    }


@pytest.mark.parametrize(
    "values,expected",
    [
        ({1: 1, 2: 0, 6: 0}, []),
        ({2: 1, 3: 1, 4: 0, 5: 0, 6: 0}, []),
        ({2: 1, 4: 1}, ["4"]),
        ({2: 1, 5: 1}, ["5"]),
        ({2: 1, 4: 1, 5: 1}, ["4", "5"]),
        ({2: 0, 4: 1}, []),
        ({2: "declined", 4: 1}, []),
        ({6: 1, 7: 0}, []),
        ({6: 1, 7: "deferred"}, []),
        ({6: 0, 7: 1}, []),
        ({6: 1, 7: 1}, ["7"]),
    ],
)
def test_contact_triggers_are_based_on_intent_or_recent_behavior_not_a_sum(
    values, expected
):
    assert safety_urgent_items(raw(values)) == expected
    result = safety_result(raw(values))
    assert result["total"] is None and result["urgent"] == bool(expected)


def assessment(store):
    with connect(store[1]) as c:
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(1,'2026-10-01','2026-10-27','2026-10-27T10:00:00+00:00')"
        ).lastrowid
        aid = c.execute(
            "INSERT INTO assessments(session_id,patient_id,scale,version) VALUES(?,1,'safety6',?)",
            (sid, SAFETY_VERSION),
        ).lastrowid
    return aid


def save(store, aid, item, value, token=None, revision=None):
    with connect(store[1]) as c:
        current = c.execute(
            "SELECT revision FROM assessments WHERE id=?", (aid,)
        ).fetchone()[0]
    return call(
        store,
        "/assessments/{aid}/save",
        "POST",
        who="p1",
        aid=aid,
        form=dict(
            csrf="direct-unit-csrf",
            intent="draft",
            step_item=str(item),
            revision=str(current if revision is None else revision),
            request_id=token or secrets.token_urlsafe(24),
            **{f"answer_{item}": str(value)},
        ),
    )


def triggered(store):
    aid = assessment(store)
    for item, value in [(1, 0), (2, 1), (3, 0), (4, 1)]:
        assert save(store, aid, item, value).status_code == 303
    return aid


def test_partial_saved_answer_creates_one_private_notice_with_results_and_header(store):
    aid = triggered(store)
    with connect(store[1]) as c:
        notices = safety_alerts.pending(c, store[1], 1)
        assert len(notices) == 1
        notice = notices[0]
        assert notice["status"] == "draft" and notice["triggers"] == ["4"]
        assert notice["answers"][3]["answer"] == "Да"
        assert not safety_alerts.pending(c, store[1], 2)
    page = call(store, "/doctor/messages")
    assert (
        page.status_code == 200
        and "Как можно скорее свяжитесь с пациентом" in page.body.decode()
    )
    assert f'href="/assessments/{aid}"' in page.body.decode()
    assert (
        "Результаты теста" in page.body.decode()
        and "Тест ещё не завершён" in page.body.decode()
    )
    assert call(store, "/doctor").context["urgent_notices"]
    for who in ("p1", "p2"):
        with pytest.raises(HTTPException) as error:
            call(store, "/api/safety-messages", who=who)
        assert error.value.status_code == 403
    assert json.loads(call(store, "/api/safety-messages", who="d2").body)["count"] == 0
    assert json.loads(call(store, "/api/safety-messages").body)["count"] == 1


def test_idempotency_new_answers_and_changed_branch_keep_the_original_urgent_evidence(
    store,
):
    aid = triggered(store)
    token = secrets.token_urlsafe(24)
    with connect(store[1]) as c:
        rev = c.execute(
            "SELECT revision FROM assessments WHERE id=?", (aid,)
        ).fetchone()[0]
    for _ in range(2):
        assert save(store, aid, 5, 1, token, rev).status_code == 303
    assert save(store, aid, 2, 0).status_code == 303
    with connect(store[1]) as c:
        notices = safety_alerts.pending(c, store[1], 1)
    assert len(notices) == 1 and notices[0]["changed_since_signal"]
    assert notices[0]["original_answers"][3]["answer"] == "Да"
    assert notices[0]["answers"][3]["answer"] == "Не задавался по правилу"


def test_acknowledgement_does_not_unpin_until_doctor_records_action_and_access_is_scoped(
    store,
):
    triggered(store)
    with connect(store[1]) as c:
        event = safety_alerts.pending(c, store[1], 1)[0]
    fields = dict(csrf="direct-unit-csrf", intent="acknowledge", action_text="")
    for who in ("p1", "d2"):
        with pytest.raises(HTTPException) as error:
            call(
                store,
                "/events/{eid}/status",
                "POST",
                who=who,
                eid=event["id"],
                form=fields,
                **fields,
            )
        assert error.value.status_code == 403
    assert (
        call(
            store,
            "/events/{eid}/status",
            "POST",
            eid=event["id"],
            form=fields,
            **fields,
        ).status_code
        == 303
    )
    assert json.loads(call(store, "/api/safety-messages").body)["count"] == 1
    action = fields | dict(
        intent="action", action_text="Связались с тестовым пациентом, обсудили ответы."
    )
    assert (
        call(
            store,
            "/events/{eid}/status",
            "POST",
            eid=event["id"],
            form=action,
            **action,
        ).status_code
        == 303
    )
    assert json.loads(call(store, "/api/safety-messages").body)["count"] == 0


def test_support_visible_from_saved_critical_answer_and_help_contacts_are_public(store):
    aid = triggered(store)
    page = call(store, "/assessments/{aid}", who="p1", aid=aid, item=5, review=False)
    html = page.body.decode()
    assert "data-safety-support  aria-live" in html
    assert 'href="/help-now"' in html and "data-safety-values=" in html
    help_page = call(store, "/help-now", who="p1").body.decode()
    for text in ("tel:051", "tel:+7495051", "+7 495 051", "tel:112"):
        assert text in help_page


def test_new_test_account_is_empty_assigned_and_never_resets_existing_accounts(store):
    with connect(store[1]) as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]
    account = create_patient(store[1], "fresh-safety", 1)
    with connect(store[1]) as c:
        row = c.execute(
            "SELECT * FROM accounts WHERE username='fresh-safety'"
        ).fetchone()
        assert verify_password(account["password"], row["password_hash"])
        assert (
            c.execute(
                "SELECT doctor_id FROM patients WHERE id=?", (account["patient_id"],)
            ).fetchone()[0]
            == 1
        )
        for table in ("assessments", "diary", "consents", "assessment_events"):
            assert (
                c.execute(
                    f"SELECT count(*) FROM {table} WHERE patient_id=?",
                    (account["patient_id"],),
                ).fetchone()[0]
                == 0
            )
        assert [
            tuple(r)
            for r in c.execute(
                "SELECT * FROM accounts WHERE username!='fresh-safety' ORDER BY id"
            )
        ] == before
    with pytest.raises(ValueError):
        create_patient(store[1], "fresh-safety", 1)


def test_create_clean_account_button_is_doctor_only_and_csrf_checked(store):
    for who, csrf in [("p1", "direct-unit-csrf"), ("d1", "wrong")]:
        with pytest.raises(HTTPException) as error:
            call(
                store,
                "/doctor/messages/test-account",
                "POST",
                who=who,
                form=dict(csrf=csrf),
            )
        assert error.value.status_code == 403
    response = call(
        store,
        "/doctor/messages/test-account",
        "POST",
        who="d2",
        form=dict(csrf="direct-unit-csrf"),
    )
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    created = response.context["credentials"]
    with connect(store[1]) as c:
        assert (
            c.execute(
                "SELECT doctor_id FROM patients WHERE id=?", (created["patient_id"],)
            ).fetchone()[0]
            == 2
        )
        assert not c.execute(
            "SELECT 1 FROM assessments WHERE patient_id=?", (created["patient_id"],)
        ).fetchone()


def test_unchanged_critical_answer_gets_first_notice_when_resaved_after_upgrade(store):
    aid = assessment(store)
    values = raw({1: 0, 2: 1, 3: 0, 4: 1})
    with connect(store[1]) as c:
        a = dict(c.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone())
        first = safety_alerts.record(
            c, store[1], a, values, values, 1, store[2]["p1"]["id"]
        )
        assert first
        assert (
            safety_alerts.record(
                c, store[1], a, values, values, 2, store[2]["p1"]["id"]
            )
            is None
        )
