"""Real per-candidate matches and context-preserving cards on fictional records."""

from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from app.services import adverse_reactions as service
from app.storage import connect
from tests.helpers import login
from tests.test_adverse_reactions import preferences as intake_preferences
from tests.test_medication_preferences import post as send_preferences
from tests.test_medicine_workspace import scheme, saved


def test_picker_checks_before_addition_and_links_to_card_for_selected_patient(client):
    login(client, "patient1")
    assert (
        send_preferences(
            client, effects=("S014", "S131", "S001"), primary="S014"
        ).status_code
        == 303
    )
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert page.context["selected"] == {}
    checks = page.context["tolerability_checks"]
    assert len(checks) == len(page.context["drugs"]) == 12
    check = checks["D01"]
    assert (check["state"], check["matched_count"], check["total"]) == ("matches", 1, 3)
    assert check["review_count"] == 1
    assert {(e["code"], e["state"], e["primary"]) for e in check["effects"]} == {
        ("S014", "listed", True),
        ("S131", "review", False),
        ("S001", "not_found", False),
    }
    assert 'data-picker-preferences="D01" data-check-state="matches"' in page.text
    assert 'aria-describedby="picker-check-D01"' in page.text
    assert "Есть в реестре" in page.text and "В реестре не найдено" in page.text
    assert "Термин найден в другой системе: Нервная система" in page.text
    assert (
        'target="_blank" rel="noopener noreferrer" data-draft-card="D01"' in page.text
    )
    assert "не отсутствие побочного эффекта" in page.text
    drug = next(d for d in page.context["drugs"] if d["code"] == "D01")
    card = client.get(drug["card_url"])
    assert card.status_code == 200 and card.context["prescription_patient"] == 1
    assert card.context["card_return"] == "/medicines/prescriptions?patient_id=1"
    assert "/medicines/prescriptions?add=D01&amp;patient_id=1" in card.text
    assert saved(client)["revision"] == 0


def test_no_preferences_or_private_drafts_never_look_like_no_side_effects(client):
    login(client, "patient1")
    assert (
        send_preferences(
            client, intent="draft", effects=("S014",), primary="S014"
        ).status_code
        == 303
    )
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert all(
        c["state"] == "not_selected" and not c["effects"]
        for c in page.context["tolerability_checks"].values()
    )
    assert "Нет выбранных эффектов для проверки" in page.text
    assert 'data-picker-effect="S014"' not in page.text


def test_latest_sent_intake_or_preferences_refresh_candidates_but_not_history(client):
    login(client, "patient1")
    assert intake_preferences(client, ["S014"], "S014", submit=True).status_code == 303
    login(client, "doctor")
    page = client.get("/patients/1/assignments?add=D01")
    assert page.context["tolerability_checks"]["D01"]["matched_count"] == 1
    assert (
        client.post(
            "/patients/1/assignments/medicines", data=scheme(client)
        ).status_code
        == 200
    )
    original = saved(client)
    login(client, "patient1")
    assert (
        send_preferences(client, effects=("S001",), primary="S001").status_code == 303
    )
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    check = page.context["tolerability_checks"]["D01"]
    assert check["state"] == "not_found" and check["matched_count"] == 0
    assert check["effects"][0]["code"] == "S001"
    assert saved(client) == original
    old = client.get("/patients/1/assignments?version=1")
    assert old.context["tolerability_checks"] == {}
    assert old.context["tolerability_matches"]["D01"][0]["code"] == "S014"
    assert 'id="draft-picker"' not in old.text
    other = client.get("/patients/2/assignments")
    assert other.context["tolerability_checks"]["D01"]["state"] == "not_selected"


def test_empty_or_other_drug_registry_does_not_claim_absence(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        rows = service.records(c)
    snapshot = dict(selected=["S014", "S131"], primary="S131")
    for missing in ([], [r for r in rows if r["drug"] != "D01"]):
        check = service.preference_check(missing, "D01", snapshot)
        assert check["state"] == "no_data" and check["matched_count"] == 0
        assert {e["state"] for e in check["effects"]} == {"unknown"}
    # Identical term in another organ system is not an exact match.
    check = service.preference_check(
        rows, "D01", dict(selected=["S131"], primary="S131")
    )
    assert check["state"] == "review" and check["effects"][0]["primary"] is True
    assert check["effects"][0]["other_systems"] == ["Нервная система"]
    assert service.matches(rows, "D01", dict(selected=["S131"], primary="S131")) == []


def test_card_return_and_prescribing_stay_with_second_patient(client):
    login(client, "doctor")
    page = client.get("/patients/2/assignments")
    drug = next(d for d in page.context["drugs"] if d["code"] == "D09")
    target = parse_qs(urlsplit(drug["card_url"]).query)["return_to"][0]
    assert target == "/medicines/prescriptions?patient_id=2"
    card = client.get(drug["card_url"])
    assert card.status_code == 200 and card.context["prescription_patient"] == 2
    assert "/medicines/prescriptions?add=D09&amp;patient_id=2" in card.text
    assert client.get(target).context["selected_patient"]["id"] == 2
    added = client.get("/medicines/prescriptions?add=D09&patient_id=2")
    assert (
        added.context["selected_patient"]["id"] == 2
        and "D09" in added.context["selected"]
    )


@pytest.mark.parametrize("value", ["x", "1&patient_id=2", "999"])
def test_invalid_or_inaccessible_card_patient_context_is_rejected(client, value):
    login(client, "doctor")
    return_to = "/medicines/prescriptions?patient_id=" + value
    response = client.get("/medicines/d09?" + urlencode(dict(return_to=return_to)))
    assert response.status_code in (403, 404, 422)


def test_patient_cannot_read_doctor_picker_or_cards(client):
    login(client, "patient1")
    response = client.get("/patients/1/assignments")
    assert response.url.path == "/patients/1/profile/assignments"
    assert "data-picker-preferences" not in response.text
    assert client.get("/medicines/d09").status_code == 403
