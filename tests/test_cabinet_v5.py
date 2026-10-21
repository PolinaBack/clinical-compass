"""Synthetic acceptance flows for separate workspaces and repeat assignments."""

import secrets
import re
from datetime import date, timedelta
from app.family import family_tree
from app.safety import SAFETY_VERSION
from tests.helpers import login
from tests.test_navigation_v4 import post
from tests.test_workflows import rows, start, save


def assign_new(client, scale="phq9", pid=1, nonce=None):
    return post(
        client,
        f"/patients/{pid}/assign",
        scale=scale,
        session_id=0,
        due_day=date.today().isoformat(),
        assignment_token=nonce or secrets.token_urlsafe(24),
    )


def test_both_initial_forms_are_offered_independently_and_banner_removed(client):
    login(client, "patient1")
    page = client.get("/patients/1/profile/assessments").text
    assert 'action="/safety/start"' in page and 'action="/assessments/start"' in page
    assert "onboarding-banner" not in client.get("/me/start").text
    start(client)
    page = client.get("/patients/1/profile/assessments").text
    assert (
        'action="/safety/start"' in page
    )  # Opening PHQ-4 never hides the safety offer.
    assert 'action="/assessments/start"' not in page


def test_specialist_sections_are_separate_and_patient_cannot_enter(client):
    login(client, "doctor")
    expected = {
        "safety": 'id="safety"',
        "assessments": 'id="assessments"',
        "care": 'id="care"',
        "conversation": 'id="conversation"',
    }
    for section, marker in expected.items():
        html = client.get(f"/patients/1/workspace/{section}").text
        assert marker in html
        for other in expected.values():
            if other != marker:
                assert other not in html
        assert "Журнал доступа" not in html and "Экстренная помощь" not in html
        nav = re.search(
            r'<nav class="tab-nav specialist-nav".*?</nav>', html, re.S
        ).group()
        assert 'href="#' not in nav
    assert client.get("/patients/1/audit").status_code == 404
    login(client, "patient1")
    assert client.get("/patients/1/workspace/assessments").status_code == 403


def test_added_relatives_have_leaves_and_keep_their_family_connection(client):
    login(client, "patient1")
    post(client, "/patients/1/relatives", name="Учебная мама", relation="mother")
    parent = rows(client, "SELECT id FROM relatives")[0]["id"]
    for i in range(5):
        assert (
            post(
                client,
                "/patients/1/relatives",
                name=f"Учебный близкий {i}",
                relation="other",
                parent_id=parent,
            ).status_code
            == 200
        )
    relatives = rows(client, "SELECT * FROM relatives")
    tree = family_tree(relatives)
    assert len(tree["family_nodes"]) == 11 and tree["family_height"] == 1080
    assert len(tree["family_connections"]) == 5
    html = client.get("/patients/1/profile/family").text
    assert html.count('class="family-leaf ') == 11
    assert "Учебный близкий 4" in html
    assert (
        len(rows(client, "SELECT * FROM relatives")) == 6
    )  # Empty base leaves do not create people.
    login(client, "patient2")
    assert (
        post(
            client,
            "/patients/2/relatives",
            name="Подмена",
            relation="other",
            parent_id=parent,
        ).status_code
        == 422
    )


def test_new_assignment_creates_new_form_and_retries_do_not_duplicate_or_reset(client):
    login(client, "patient1")
    old = start(client)
    save(client, old, [0, 0, 0, 0])
    old_answers = rows(client, "SELECT * FROM answers WHERE assessment_id=?", (old,))
    sid = rows(client, "SELECT session_id FROM assessments WHERE id=?", (old,))[0][
        "session_id"
    ]
    login(client, "doctor")
    assert (
        post(
            client,
            "/patients/1/assign",
            scale="phq4",
            session_id=sid,
            due_day=date.today().isoformat(),
        ).status_code
        == 409
    )
    nonce = secrets.token_urlsafe(24)
    assert assign_new(client, "phq4", nonce=nonce).status_code == 200
    assert assign_new(client, "phq4", nonce=nonce).status_code == 200
    forms = rows(client, "SELECT * FROM assessments ORDER BY id")
    assert len(forms) == 2 and forms[1]["session_id"] != sid
    assert forms[1]["status"] == "draft" and forms[1]["assigned_by"]
    assert assign_new(client, "phq9", nonce=nonce).status_code == 409
    login(client, "patient1")
    assert (
        f'/assessments/{forms[1]["id"]}'
        in client.get("/patients/1/profile/assessments").text
    )
    save(client, forms[1]["id"], [1, 1, 1, 1])
    login(client, "doctor")
    assert assign_new(client, "phq4", nonce=nonce).status_code == 200
    assert (
        rows(client, "SELECT status FROM assessments WHERE id=?", (forms[1]["id"],))[0][
            "status"
        ]
        == "completed"
    )
    assert (
        rows(client, "SELECT * FROM answers WHERE assessment_id=?", (old,))
        == old_answers
    )


def test_fresh_patient_can_receive_new_safety_or_package_and_disabled_forms_are_rejected(
    client,
):
    login(client, "doctor")
    assert assign_new(client, "safety6").status_code == 200
    safety = rows(client, "SELECT * FROM assessments")[0]
    assert (
        safety["version"] == SAFETY_VERSION
        and safety["due_day"] == date.today().isoformat()
    )
    assert assign_new(client, "phqads").status_code == 200
    pair = rows(client, "SELECT * FROM assessments WHERE scale!='safety6'")
    assert {r["scale"] for r in pair} == {"phq9", "gad7"} and len(
        {r["session_id"] for r in pair}
    ) == 1
    count = len(rows(client, "SELECT * FROM assessment_sessions"))
    for key in ["hads", "hdrs17", "madrs"]:
        assert assign_new(client, key).status_code == 422
    assert (
        post(
            client,
            "/patients/1/assign",
            scale="phq9",
            session_id=0,
            assignment_token=secrets.token_urlsafe(24),
            due_day=(date.today() - timedelta(days=1)).isoformat(),
        ).status_code
        == 422
    )
    assert len(rows(client, "SELECT * FROM assessment_sessions")) == count


def test_dynamics_only_contains_completed_results_and_reports(client):
    login(client, "patient1")
    phq4 = start(client)
    save(client, phq4, [1, 0, 0, 1])
    login(client, "doctor")
    assign_new(client, "phq9")
    draft = rows(client, "SELECT id FROM assessments WHERE scale='phq9'")[0]["id"]
    post(
        client,
        "/patients/1/decisions",
        category="plan",
        body="Учебное решение",
        source="Синтетический сценарий",
        confirmed="true",
    )
    login(client, "patient1")
    html = client.get("/patients/1/results").text
    assert f"/assessments/{phq4}" in html
    assert f"/assessments/{draft}" not in html
    assert "Баллы PHQ-4" in html and "Баллы PHQ-9" not in html
    assert "Учебное решение" not in html and "PHQ-ADS" not in html
    assert "reports" in html and "Не рассчитывается" not in html


def test_catalog_has_uniform_tiles_and_medicine_library_is_role_scoped(client):
    login(client, "doctor")
    html = client.get("/clinical-tools?patient_id=1").text
    assert html.count('class="card tool-tile"') == 9
    for key in ["safety6", "phq4", "hcl33", "hads", "hdrs17", "madrs"]:
        assert f'data-tool="{key}"' in html
    assert 'href="/reference/aviandr"' not in html
    assert '<option value="0">Новое обследование</option>' in html
    html = client.get("/medicines").text
    assert 'href="/medicines/aviandr"' in html and "Экстренная помощь" not in html
    assert client.get("/medicines/aviandr").status_code == 200
    assert client.get("/medicines/not-real").status_code == 404
    login(client, "patient1")
    assert client.get("/medicines").status_code == 403
    assert client.get("/medicines/aviandr").status_code == 403
