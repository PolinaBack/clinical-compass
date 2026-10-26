"""Saved requested sections are delivered without publishing unrelated drafts."""

import json
from app.services import intake
from app.storage import connect
from tests.helpers import login, token
from tests.test_intake import save, send, entity, form
from tests.test_specialist_requests import request_info


def requests(client):
    with connect(client.app.state.db_path) as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM patient_information_requests ORDER BY id")
        ]


def latest(client):
    with connect(client.app.state.db_path) as c:
        return intake.read_submission(c, 1)


def test_saved_requested_medicines_close_both_views_without_sending_personal_health_or_allergy_drafts(
    client,
):
    login(client, "patient1")
    save(client, "personal", a_full_name="PRIVATE_PERSONAL")
    save(client, "health", a_known_conditions="Да", a_description="PRIVATE_HEALTH")
    entity(client, "reaction", a_name="PRIVATE_ALLERGY")
    login(client, "doctor")
    request_info(client, "medicines")
    request_info(client, "conditions")
    login(client, "patient1")
    response = save(client, "medicines", a_current="Нет")
    assert (
        response.status_code == 200
        and "Ответ по запросу врача отправлен" in response.text
    )
    rows = requests(client)
    assert rows[0]["answered_at"] and rows[0]["submission_id"]
    assert rows[1]["answered_at"] is None
    source = latest(client)
    text = json.dumps(source["data"], ensure_ascii=False)
    assert all(
        secret not in text
        for secret in ["PRIVATE_PERSONAL", "PRIVATE_HEALTH", "PRIVATE_ALLERGY"]
    )
    assert source["data"]["answers"]["medicines"]["current"]["value"] == "Нет"
    page = client.get("/patients/1/profile/about")
    assert f'data-information-request="{rows[0]["id"]}"' not in page.text
    assert f'data-information-request="{rows[1]["id"]}"' in page.text
    login(client, "doctor")
    status = client.get("/api/patients/1/information-requests")
    assert (
        status.status_code == 200 and status.json()["states"]["medicines"] == "received"
    )
    assert "PRIVATE_PERSONAL" not in status.text
    page = client.get("/patients/1/profile")
    assert (
        'data-information-request-control="medicines" data-information-state="received"'
        in page.text
    )
    assert "Открыть сведения" in page.text and "Ответ по запросу врача" in page.text


def test_async_drafts_stay_private_until_explicit_section_save_and_later_full_submission_still_sends_other_changes(
    client,
):
    login(client, "patient1")
    save(client, "personal", a_full_name="PRIVATE_PENDING_NAME")
    login(client, "doctor")
    request_info(client)
    login(client, "patient1")
    autosave = client.post(
        "/patients/1/intake/sections/medicines",
        data=form(client, a_current="Нет"),
        headers={"Accept": "application/json"},
    )
    assert autosave.status_code == 200 and not requests(client)[0]["answered_at"]
    assert latest(client) is None
    assert save(client, "medicines", a_current="Нет").status_code == 200
    partial = latest(client)
    assert "PRIVATE_PENDING_NAME" not in json.dumps(partial["data"], ensure_ascii=False)
    assert send(client).status_code == 200
    full = latest(client)
    assert full["id"] != partial["id"]
    assert (
        full["data"]["answers"]["personal"]["full_name"]["value"]
        == "PRIVATE_PENDING_NAME"
    )
    assert send(client).status_code == 200
    assert latest(client)["id"] == full["id"]
    with connect(client.app.state.db_path) as c:
        original = json.loads(
            c.execute(
                "SELECT payload FROM intake_submissions WHERE id=?", (partial["id"],)
            ).fetchone()[0]
        )
        assert "PRIVATE_PENDING_NAME" not in json.dumps(original, ensure_ascii=False)


def test_saved_medicine_card_answers_only_medicines_and_keeps_private_reaction_and_links_out(
    client,
):
    login(client, "patient1")
    entity(client, "reaction", a_name="PRIVATE_REACTION")
    login(client, "doctor")
    request_info(client, "medicines")
    request_info(client, "allergies")
    login(client, "patient1")
    response = entity(
        client,
        "medication",
        a_name="Препарат из ответа",
        a_category="Назначено, принимаю сейчас",
        related="1",
    )
    assert response.status_code == 200
    rows = requests(client)
    assert rows[0]["answered_at"] and rows[1]["answered_at"] is None
    data = latest(client)["data"]
    assert [e["kind"] for e in data["entities"]] == ["medication"]
    assert data["links"] == []
    assert "PRIVATE_REACTION" not in json.dumps(data, ensure_ascii=False)
    login(client, "doctor")
    assert "Препарат из ответа" in client.get("/patients/1/profile").text


def test_medicine_card_autosave_stays_private_until_patient_saves_card(client):
    login(client, "doctor")
    request_info(client)
    login(client, "patient1")
    response = client.post(
        "/patients/1/intake/entities/medication/save",
        data=form(client, a_name="Личный черновик лекарства"),
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved["information_sent"] == []
    assert latest(client) is None and requests(client)[0]["answered_at"] is None
    assert (
        entity(
            client,
            "medication",
            entity_id=str(saved["entity_id"]),
            entity_version=str(saved["entity_version"]),
            a_name="Личный черновик лекарства",
        ).status_code
        == 200
    )
    assert requests(client)[0]["answered_at"]
    assert (
        latest(client)["data"]["entities"][0]["data"]["name"]["value"]
        == "Личный черновик лекарства"
    )


def test_unchanged_response_reuses_submission_and_unrequested_saves_never_send_it(
    client,
):
    login(client, "patient1")
    save(client, "medicines", a_current="Нет")
    send(client)
    original = latest(client)["id"]
    save(client, "personal", a_full_name="PRIVATE_NEW_DRAFT")
    login(client, "doctor")
    request_info(client)
    login(client, "patient1")
    assert save(client, "personal", a_full_name="PRIVATE_NEW_DRAFT").status_code == 200
    assert requests(client)[0]["answered_at"] is None
    assert save(client, "medicines", a_current="Нет").status_code == 200
    assert latest(client)["id"] == original and requests(client)[0]["answered_at"]
    assert "PRIVATE_NEW_DRAFT" not in json.dumps(
        latest(client)["data"], ensure_ascii=False
    )


def test_empty_saves_cannot_close_requests_and_status_endpoint_keeps_role_and_csrf_scope(
    client,
):
    assert client.get("/api/patients/1/information-requests").status_code == 401
    login(client, "doctor")
    request_info(client)
    login(client, "patient1")
    assert save(client, "medicines").status_code == 200
    assert requests(client)[0]["answered_at"] is None and latest(client) is None
    assert (
        client.post(
            "/patients/1/intake/sections/medicines",
            data=form(client, a_current="Нет") | {"csrf": "wrong"},
        ).status_code
        == 403
    )
    assert requests(client)[0]["answered_at"] is None
    login(client, "patient2")
    assert client.get("/api/patients/1/information-requests").status_code == 403
    assert client.get("/api/patients/2/information-requests").json()["pending"] == []


def test_partial_response_preserves_previously_sent_information_and_hides_new_unrequested_edits(
    client,
):
    login(client, "patient1")
    save(client, "personal", a_full_name="Прежнее отправленное имя")
    entity(client, "reaction", a_name="Прежняя отправленная реакция")
    send(client)
    original = latest(client)
    save(client, "personal", a_full_name="PRIVATE_CHANGED_NAME")
    entity(client, "reaction", a_name="PRIVATE_NEW_REACTION")
    login(client, "doctor")
    request_info(client)
    login(client, "patient1")
    save(client, "medicines", a_current="Нет")
    data = latest(client)["data"]
    assert data["answers"]["personal"] == original["data"]["answers"]["personal"]
    assert data["entities"] == original["data"]["entities"]
    assert all(
        word not in json.dumps(data, ensure_ascii=False)
        for word in ("PRIVATE_CHANGED_NAME", "PRIVATE_NEW_REACTION")
    )
    assert requests(client)[0]["answered_at"]
