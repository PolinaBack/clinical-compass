"""Synthetic UI/data acceptance. Not validation of drug safety or frequency."""

from collections import Counter
from urllib.parse import urlencode
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.adverse_catalog import OPTIONS, ELIGIBLE
from app.intake_catalog import VERSION
from app.services import adverse_reactions as service, intake
from app.storage import connect
from tests.helpers import login, token
from tests.test_medicine_workspace import scheme, saved


def preferences(client, selected, primary="", pid=1, submit=False):
    with connect(client.app.state.db_path) as c:
        revision = intake.draft(c, pid)["revision"]
    response = client.post(
        f"/patients/{pid}/intake/sections/medicines",
        data=dict(
            csrf=token(client),
            revision=revision,
            catalog_version=VERSION,
            a_avoid_reactions=selected,
            s_avoid_reactions="answered" if selected else "unanswered",
            a_primary_reaction=primary,
            s_primary_reaction="answered" if primary else "unanswered",
        ),
        follow_redirects=False,
    )
    if submit and response.status_code == 303:
        with connect(client.app.state.db_path) as c:
            revision = intake.draft(c, pid)["revision"]
        return client.post(
            f"/patients/{pid}/intake/submit",
            data=dict(csrf=token(client), revision=revision, catalog_version=VERSION),
            follow_redirects=False,
        )
    return response


def test_all_records_terms_frequencies_and_preference_dictionary(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        rows = service.records(c)
    assert len(rows) == 468 and len({r["key"] for r in rows}) == 468
    assert len({r["system"] for r in rows}) == 15
    assert sum(r["frequency"] != "частота требует уточнения" for r in rows) == 34
    assert Counter(r["drug"] for r in rows) == dict(
        D01=21,
        D02=45,
        D03=50,
        D04=50,
        D05=44,
        D06=43,
        D07=38,
        D08=41,
        D09=37,
        D10=32,
        D11=31,
        D12=36,
    )
    assert len(OPTIONS) == 163 and len(ELIGIBLE) == 138
    assert sum(not o["allowed"] for o in OPTIONS) == 25
    assert all(o["medical"] and o["label"] and o["system"] for o in OPTIONS)
    assert len(service.filtered(rows, drug="D01", system="ЖКТ")) == 3
    assert len(service.filtered(rows, drug="D11", system="ЖКТ")) == 5
    assert {r["medical"] for r in service.filtered(rows, q="покалывание")} == {
        "парестезия"
    }


@pytest.mark.parametrize(
    "params, count",
    [
        ({}, 468),
        ({"drug": "D01"}, 21),
        ({"drug": "D11"}, 31),
        ({"drug": "D01", "system": "ЖКТ"}, 3),
        ({"drug": "D11", "system": "ЖКТ"}, 5),
        ({"q": "тошнота"}, 12),
        ({"q": "НЕСУЩЕСТВУЮЩИЙТЕРМИН"}, 0),
    ],
)
def test_registry_filters_and_no_pdf_citation(client, params, count):
    login(client, "doctor")
    page = client.get("/medicines/adverse-effects", params=params)
    assert page.status_code == 200 and page.context["count"] == count
    assert page.context["total"] == 468 and len(page.context["groups"]) == 15
    assert (
        "Клинический_компас_v11" not in page.text
        and "побочные_эффекты_по_системам.pdf" not in page.text
    )
    assert "library_theme.css" in page.text
    assert "data-adverse-row" in page.text
    if count == 0:
        assert "В данной неполной базе совпадений не найдено" in page.text
    assert client.get("/medicines/adverse-effects?drug=D99").status_code == 422


@pytest.mark.parametrize(
    "codes, primary",
    [
        (["S014", "S055", "S072", "S115"], "S014"),
        (["S006"], "S006"),
        (["S014"], "S072"),
        (["S014", "S014"], "S014"),
    ],
)
def test_invalid_tolerability_does_not_save(client, codes, primary):
    login(client, "patient1")
    assert preferences(client, codes, primary).status_code == 422
    with connect(client.app.state.db_path) as c:
        assert "avoid_reactions" not in intake.draft(c, 1)["answers"].get(
            "medicines", {}
        )


def test_only_sent_preferences_match_this_patients_draft_and_history(client):
    login(client, "patient1")
    assert preferences(client, ["S014", "S055", "S072"], "S014").status_code == 303
    login(client, "doctor")
    page = client.get("/medicines/prescriptions?patient_id=1&add=D01")
    assert (
        page.context["tolerability_choices"] == []
    )  # private unfinished intake stays private
    login(client, "patient1")
    assert (
        preferences(client, ["S014", "S055", "S072"], "S014", submit=True).status_code
        == 303
    )
    login(client, "doctor")
    page = client.get("/medicines/prescriptions?patient_id=1&add=D01")
    assert len(page.context["tolerability_choices"]) == 3
    matches = page.context["tolerability_matches"]["D01"]
    assert {r["code"] for r in matches} == {"S014", "S055", "S072"}
    assert next(r for r in matches if r["primary"])["code"] == "S014"
    assert "★ Тошнота" in page.text
    assert (
        client.post("/medicines/prescriptions/save", data=scheme(client)).status_code
        == 200
    )
    assert len(saved(client)["items"][0]["preference_matches"]) == 3
    # No copy across cards, and exact organ system matters even with identical terms.
    other = client.get("/medicines/prescriptions?patient_id=2&add=D01")
    assert other.context["tolerability_choices"] == []
    with connect(client.app.state.db_path) as c:
        assert (
            service.matches(
                service.records(c), "D01", dict(selected=["S131"], primary="S131")
            )
            == []
        )  # sleep/psych, not nervous system
    login(client, "patient1")
    assert preferences(client, ["S014"], "S014", submit=True).status_code == 303
    login(client, "doctor")
    current = client.get("/medicines/prescriptions?patient_id=1")
    assert len(current.context["tolerability_choices"]) == 1
    old = client.get("/medicines/prescriptions?patient_id=1&version=1")
    assert len(old.context["tolerability_choices"]) == 3
    form = scheme(client, revision=1)
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    assert saved(client)["revision"] == 2
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert (
            len(
                restarted.get("/medicines/prescriptions?patient_id=1").context[
                    "tolerability_choices"
                ]
            )
            == 1
        )


def test_optional_choice_no_js_security_and_text_labels(client):
    login(client, "patient1")
    assert preferences(client, []).status_code == 303
    assert preferences(client, ["S014"], "S014", submit=True).status_code == 303
    page = client.get("/patients/1/profile/about/medicines")
    assert "data-tolerability-picker" in page.text and 'value="S014"' in page.text
    with connect(client.app.state.db_path) as c:
        assert "Головная боль" in {o["label"] for o in ELIGIBLE.values()}
    assert client.get("/medicines/adverse-effects").status_code == 403
    login(client, "doctor")
    page = client.get("/patients/1/profile/about/medicines")
    assert "Тошнота" in page.text and "<p>S014</p>" not in page.text
    assert preferences(client, ["S014"], "S014").status_code == 403
    assert client.get("/doctor/library").text.count('class="library-section"') == 4
