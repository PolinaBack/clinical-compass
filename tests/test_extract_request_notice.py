"""Doctor notices track unanswered patient input; saved documents remain private."""

import secrets

import pytest
from app.services import extract_requests as requests
from app.storage import connect
from tests.test_diary_library_offline import store, call
from tests.test_extract_requests import new_form
from tests.test_patient_extracts_direct import current, form_for, save


def submit(store):
    _, path, users = store
    with connect(path) as c:
        return requests.submit(c, path, users["p1"], 1, new_form(), [])


def request_item(store, rid):
    _, path, users = store
    with connect(path) as c:
        return requests.get(c, path, users["d1"], 1, rid)


def reply(store, rid, who="d1", **changes):
    form = dict(
        csrf="direct-unit-csrf",
        revision=request_item(store, rid)["revision"],
        client_token=secrets.token_urlsafe(24),
        status="clarify",
        reply="Уточните получателя",
    )
    form.update(changes)
    return call(
        store,
        "/patients/{pid}/profile/extracts/requests/{rid}/reply",
        "POST",
        who=who,
        pid=1,
        rid=rid,
        form=form,
    )


def dashboard(store, count, who="d1"):
    page = call(store, "/doctor", who=who)
    assert page.context["pending_extract_requests"] == count
    assert ("data-extract-request-notice" in page.body.decode()) == (count > 0)
    return page


def test_saved_replies_hide_notice_without_deleting_requests_and_patient_input_reopens_it(
    store,
):
    dashboard(store, 0)
    first, second = submit(store), submit(store)
    dashboard(store, 2)
    dashboard(store, 0, who="d2")
    assert (
        call(
            store, "/patients/{pid}/profile/extracts/requests/{rid}", pid=1, rid=first
        ).status_code
        == 200
    )
    dashboard(store, 2)  # Opening the request does not acknowledge it.
    assert reply(store, first, status="ready", reply="").status_code == 422
    dashboard(store, 2)
    assert (
        reply(store, first, status="in_progress", reply="Данные сохранены").status_code
        == 303
    )
    dashboard(store, 1)
    assert request_item(store, first)["status"] == "in_progress"
    assert reply(store, second).status_code == 303
    dashboard(store, 0)
    assert len(call(store, "/doctor/extract-requests").context["items"]) == 2
    assert (
        reply(store, second, who="p1", reply="Получатель — учебный отдел").status_code
        == 303
    )
    dashboard(store, 1)
    assert (
        reply(
            store, second, status="ready", reply="Документ можно получить"
        ).status_code
        == 303
    )
    dashboard(store, 0)
    assert len(request_item(store, second)["events"]) == 3


@pytest.mark.parametrize(
    "intent", ["save", "print", "publish", "refresh", "quick_print"]
)
def test_document_save_acknowledges_only_linked_request_after_success_and_preserves_publication_rules(
    store, intent
):
    rid = submit(store)
    other = submit(store)
    dashboard(store, 2)
    result = call(
        store,
        "/patients/{pid}/profile/extracts/requests/{rid}/draft",
        "POST",
        pid=1,
        rid=rid,
        form=dict(csrf="direct-unit-csrf", revision=1),
    )
    assert result.status_code == 303
    eid = request_item(store, rid)["document_id"]
    assert (
        reply(store, rid, who="p1", reply="Уточнение перед оформлением").status_code
        == 303
    )
    dashboard(store, 2)
    doc = current(store, eid)
    assert save(store, eid, form_for(doc, intent="preview")).status_code == 200
    dashboard(store, 2)
    assert save(store, eid, form_for(doc, title="Неверный вид")).status_code == 422
    dashboard(store, 2)
    assert (
        save(store, eid, form_for(doc, revision=doc["revision"] - 1)).status_code == 409
    )
    dashboard(store, 2)
    if intent == "quick_print":
        result = call(
            store,
            "/patients/{pid}/profile/extracts/{eid}/print",
            "POST",
            pid=1,
            eid=eid,
            form=dict(
                csrf="direct-unit-csrf",
                revision=doc["revision"],
                reviewed="yes",
                specialist="Тестовый специалист",
            ),
        )
    else:
        result = save(
            store,
            eid,
            form_for(
                doc, intent=intent, reviewed="yes", specialist="Тестовый специалист"
            ),
        )
    assert result.status_code == 303
    dashboard(store, 1)
    assert request_item(store, other)["events"] == []
    item = request_item(store, rid)
    assert bool(item["published"]) == (intent == "publish")
    assert item["status"] == ("ready" if intent == "publish" else "in_progress")
    assert len(call(store, "/doctor/extract-requests").context["items"]) == 2
    if intent != "publish":
        assert (
            reply(store, rid, who="p1", reply="Ещё одно уточнение").status_code == 303
        )
        dashboard(store, 2)
