"""Multiple separately scoped documents in one order, and quick print from a request."""

import secrets
from datetime import timedelta
from app.storage import connect
from app.services.clinical_forms import today
from tests.helpers import login, token
from tests.test_extract_requests import new_form, get, request_id, send
from tests.test_patient_extracts_http import current as current_document
from tests.test_patient_extracts_direct import form_for


def batch(**changes):
    data = new_form()
    data["document_count"] = "3"
    for index, (kind, purpose, recipient) in enumerate(
        [
            ("certificate", "work", "Работодатель"),
            ("certificate", "education", "Университет"),
            ("referral", "specialist", "Другой специалист"),
        ]
    ):
        for key, value in dict(
            kind=kind,
            purpose=purpose,
            purpose_other=recipient,
            due="next_visit",
            due_date="",
        ).items():
            data[f"doc_{index}_{key}"] = value
    data.update(changes)
    return data


def ids(client):
    with connect(client.app.state.db_path) as c:
        return [r[0] for r in c.execute("SELECT id FROM extract_requests ORDER BY id")]


def shortcut(client, rid):
    return client.post(
        f"/patients/1/profile/extracts/requests/{rid}/draft",
        data=dict(
            csrf=token(client), revision=get(client, rid)["revision"], intent="print"
        ),
    )


def print_form(client, eid, **changes):
    doc = current_document(client, eid)
    data = dict(
        csrf=token(client),
        revision=doc["revision"],
        organization="Тестовая организация",
        specialist=doc["data"]["specialist"],
        identity_full_name=doc["data"]["identity"]["full_name"],
        identity_birth_date=doc["data"]["identity"]["birth_date"],
        reviewed="yes",
    )
    data.update(changes)
    return data


def test_batch_same_kind_different_recipients_shared_file_once_and_idempotent_submission(
    client,
):
    login(client, "patient1")
    fields = batch()
    result = send(
        client, fields, ("order.txt", b"Common order attachment", "text/plain")
    )
    assert result.status_code == 200 and result.url.query == b"sent_count=3"
    assert "Заказ отправлен специалисту. Документов: 3" in result.text
    saved = ids(client)
    assert len(saved) == 3
    assert send(client, fields).status_code == 200 and ids(client) == saved
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT count(*) FROM extract_request_files").fetchone()[0] == 1
        )
    for index, rid in enumerate(saved):
        item = get(client, rid)
        assert (
            item["data"]["batch_position"] == index + 1
            and item["data"]["batch_total"] == 3
        )
        assert item["files"][0]["request_id"] == saved[0]
        assert (
            f"/requests/{saved[0]}/files/"
            in client.get(f"/patients/1/profile/extracts/requests/{rid}").text
        )
    login(client, "doctor")
    assert len(client.get("/doctor/extract-requests").context["items"]) == 3
    for rid, expected in zip(
        saved,
        [
            "Для работы: Работодатель",
            "Для образовательного учреждения: Университет",
            "Для другого специалиста: Другой специалист",
        ],
    ):
        response = client.post(
            f"/patients/1/profile/extracts/requests/{rid}/draft",
            data=dict(csrf=token(client), revision=1),
        )
        assert response.status_code == 200
        assert (
            current_document(client, get(client, rid)["document_id"])["data"][
                "recipient"
            ]
            == expected
        )


def test_invalid_second_row_has_no_partial_orders_and_preserves_all_rows(client):
    login(client, "patient1")
    fields = batch(
        doc_1_due="date", doc_1_due_date="", comment="Общий комментарий не терять"
    )
    response = send(client, fields)
    assert response.status_code == 422 and "Документ 2" in response.text
    assert (
        'name="doc_2_kind"' in response.text
        and "Общий комментарий не терять" in response.text
    )
    assert "Университет" in response.text and "Работодатель" in response.text
    assert ids(client) == []
    fields["doc_1_due_date"] = (today() + timedelta(days=3)).isoformat()
    assert send(client, fields).status_code == 200 and len(ids(client)) == 3


def test_batch_limits_and_role_checks_cannot_make_foreign_or_unbounded_orders(client):
    login(client, "patient1")
    for value in ["0", "51", "wrong"]:
        assert send(client, batch(document_count=value)).status_code == 422
    assert ids(client) == []
    login(client, "patient2")
    assert send(client, batch()).status_code == 403
    login(client, "doctor")
    assert send(client, batch()).status_code == 403
    assert ids(client) == []


def test_quick_print_starts_at_request_preview_confirms_on_same_page_and_preserves_content(
    client,
):
    login(client, "patient1")
    assert send(client).status_code == 200
    rid = request_id(client)
    login(client, "doctor")
    detail = client.get(f"/patients/1/profile/extracts/requests/{rid}")
    assert "Оформить документ" in detail.text and "Перейти к печати" in detail.text
    assert detail.text.index("Перейти к печати") < detail.text.index(
        'class="extract-request-data"'
    )
    preview = shortcut(client, rid)
    assert preview.status_code == 200 and preview.url.path.endswith("/print")
    assert (
        "Подтвердить и печатать" in preview.text and 'name="reviewed"' in preview.text
    )
    assert "data-extract-editor" not in preview.text
    eid = get(client, rid)["document_id"]
    before = current_document(client, eid)
    assert (
        client.get(preview.url).status_code == 200
        and current_document(client, eid)["revision"] == before["revision"]
    )
    invalid = client.post(
        f"/patients/1/profile/extracts/{eid}/print",
        data=print_form(client, eid, reviewed=""),
    )
    assert invalid.status_code == 422 and "Подтвердить и печатать" in invalid.text
    assert "Я проверил(а) сведения и макет документа" in invalid.text
    assert current_document(client, eid)["revision"] == before["revision"]
    printed = client.post(
        f"/patients/1/profile/extracts/{eid}/print",
        data=print_form(client, eid, identity_full_name="ФИО для печати"),
    )
    assert printed.status_code == 200 and "data-auto-print" in printed.text
    assert "ФИО для печати" in printed.text and "Тестовая организация" in printed.text
    after = current_document(client, eid)
    assert after["data"]["source"]["sections"] == before["data"]["source"]["sections"]
    assert after["data"]["additional"] == before["data"]["additional"]
    assert not after["published_revision"] and not get(client, rid)["published"]
    assert get(client, rid)["status"] == "in_progress"
    assert shortcut(client, rid).status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM patient_extracts").fetchone()[0] == 1


def test_print_stale_confirmation_preserves_values_and_does_not_replace_published_version(
    client,
):
    login(client, "patient1")
    send(client)
    rid = request_id(client)
    login(client, "doctor")
    shortcut(client, rid)
    eid = get(client, rid)["document_id"]
    old = print_form(client, eid, organization="Старая форма для копирования")
    doc = current_document(client, eid)
    assert (
        client.post(
            f"/patients/1/profile/extracts/{eid}/save",
            data=form_for(
                doc,
                intent="publish",
                reviewed="yes",
                csrf=token(client),
                conclusion="Опубликованный текст",
            ),
        ).status_code
        == 200
    )
    published = current_document(client, eid)["published_revision"]
    stale = client.post(f"/patients/1/profile/extracts/{eid}/print", data=old)
    assert stale.status_code == 409 and "Старая форма для копирования" in stale.text
    assert "Открыть актуальный макет" in stale.text
    assert (
        client.post(
            f"/patients/1/profile/extracts/{eid}/print",
            data=print_form(client, eid, organization="Новая печатная версия"),
        ).status_code
        == 200
    )
    assert current_document(client, eid)["published_revision"] == published
    login(client, "patient1")
    own = client.get(f"/patients/1/profile/extracts/{eid}")
    assert (
        "Новая печатная версия" not in own.text and "Опубликованный текст" in own.text
    )
    assert (
        client.post(
            f"/patients/1/profile/extracts/{eid}/print",
            data=dict(csrf=token(client), revision=1, reviewed="yes"),
        ).status_code
        == 403
    )


def test_shared_order_file_does_not_share_clinician_reply_files_between_requests(
    client,
):
    login(client, "patient1")
    send(client, batch(), ("common.txt", b"Common", "text/plain"))
    saved = ids(client)
    login(client, "doctor")
    assert (
        client.post(
            f"/patients/1/profile/extracts/requests/{saved[0]}/reply",
            data=dict(
                csrf=token(client),
                revision=1,
                client_token=secrets.token_urlsafe(24),
                status="in_progress",
                reply="Ответ только на первую справку",
            ),
            files={"file": ("first-only.txt", b"Reply to first", "text/plain")},
        ).status_code
        == 200
    )
    assert len(get(client, saved[0])["files"]) == 2
    for rid in saved[1:]:
        item = get(client, rid)
        assert len(item["files"]) == 1 and item["files"][0]["filename"] == "common.txt"
