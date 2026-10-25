"""Patient-authorized sharing, inline draft persistence and compatibility, fictional only."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token
from tests.test_medicine_workspace import scheme, saved
from tests.test_adverse_reactions import preferences as legacy_preferences
from scripts.backup import backup


def form(
    client, *, revision=0, effects=("S014",), primary="S014", intent="send", **extra
):
    return dict(
        csrf=token(client),
        revision=revision,
        effect=list(effects),
        primary=primary,
        intent=intent,
        comment="Пожелание для обсуждения",
        **extra,
    )


def post(client, *, pid=1, **changes):
    return client.post(
        f"/patients/{pid}/assignments/preferences",
        data=form(client, **changes),
        follow_redirects=False,
    )


def versions(client):
    with connect(client.app.state.db_path) as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM medication_preference_versions ORDER BY patient_id,revision"
            )
        ]


def test_medicine_editor_is_in_assignments_with_original_draft_and_history(client):
    login(client, "doctor")
    old = client.get(
        "/medicines/prescriptions?patient_id=1&add=D01", follow_redirects=False
    )
    assert (
        old.status_code == 303
        and old.headers["location"] == "/patients/1/assignments?add=D01#medicines"
    )
    page = client.get(old.headers["location"])
    assert page.context["selected"]["D01"] == dict(code="D01")
    assert page.text.index('id="medicines"') < page.text.index("data-care-form")
    assert 'action="/patients/1/assignments/medicines"' in page.text
    assert (
        '/medicines/prescriptions"'
        not in client.get("/medicines")
        .text.split('<nav class="medicine-nav"')[1]
        .split("</nav>")[0]
    )
    data = scheme(client)
    assert (
        client.post("/patients/1/assignments/medicines", data=data).url.path
        == "/patients/1/assignments"
    )
    assert saved(client)["revision"] == 1
    assert "СИНТЕТИЧЕСКАЯ ДОЗА" in client.get("/patients/1/assignments").text
    assert client.get("/patients/1/assignments?version=1").context["readonly"] is True
    assert (
        client.post("/patients/2/assignments/medicines", data=data).status_code == 422
    )
    assert client.get("/patients/2/assignments").context["draft"]["items"] == []


def test_private_preference_draft_then_explicit_send_does_not_send_intake(client):
    login(client, "patient1")
    legacy_preferences(client, ["S115"], "S115", submit=False)
    assert (
        post(client, intent="draft", effects=("S014", "S055"), primary="").status_code
        == 303
    )
    page = client.get("/patients/1/profile/assignments")
    assert page.context["medication_preferences"]["selected"] == ["S014", "S055"]
    assert page.context["medication_preferences"]["status"] == "draft"
    assert page.context["preference_sent"]["selected"] == []
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert (
        page.context["tolerability_choices"] == []
        and "Пожелание для обсуждения" not in page.text
    )
    login(client, "patient1")
    assert (
        post(client, revision=1, effects=("S014", "S055"), primary="S055").status_code
        == 303
    )
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM intake_submissions").fetchone()[0] == 0
        assert (
            b"S014"
            not in c.execute(
                "SELECT payload FROM medication_preference_versions LIMIT 1"
            ).fetchone()[0]
        )
    login(client, "doctor")
    page = client.get("/patients/1/assignments?add=D01")
    assert {c["code"] for c in page.context["tolerability_choices"]} == {"S014", "S055"}
    assert "★ Головная боль" in page.text
    assert "Пожелание для обсуждения" in page.text
    assert len(page.context["tolerability_matches"]["D01"]) == 2
    assert (
        client.post(
            "/patients/1/assignments/medicines", data=scheme(client)
        ).status_code
        == 200
    )
    assert saved(client)["tolerability"]["preference_revision"] == 2
    assert saved(client)["tolerability"]["comment"] == "Пожелание для обсуждения"
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert (
            restarted.get("/patients/1/profile/assignments").context[
                "medication_preferences"
            ]["primary"]
            == "S055"
        )


def test_further_draft_stays_private_until_sent_and_history_is_preserved(client):
    login(client, "patient1")
    assert post(client).status_code == 303
    assert (
        post(
            client, revision=1, effects=("S072",), primary="S072", intent="draft"
        ).status_code
        == 303
    )
    login(client, "doctor")
    assert client.get("/patients/1/assignments").context["tolerability"][
        "selected"
    ] == ["S014"]
    login(client, "patient1")
    assert (
        post(client, revision=2, effects=("S072",), primary="S072").status_code == 303
    )
    assert len(versions(client)) == 3
    login(client, "doctor")
    assert client.get("/patients/1/assignments").context["tolerability"][
        "selected"
    ] == ["S072"]


@pytest.mark.parametrize(
    "changes",
    [
        dict(effects=("S014", "S055", "S072", "S115")),
        dict(effects=("S014", "S014")),
        dict(effects=("S006",), primary="S006"),
        dict(primary="S072"),
        dict(primary=""),
        dict(revision=-1),
    ],
)
def test_invalid_selection_cannot_save(client, changes):
    login(client, "patient1")
    assert post(client, **changes).status_code == 422
    assert versions(client) == []


def test_patient_only_csrf_other_card_and_consent(client):
    login(client, "doctor")
    assert post(client).status_code == 403
    login(client, "patient2")
    assert post(client).status_code == 403
    login(client, "patient1", consent=False)
    assert post(client).status_code == 403
    login(client, "patient1")
    data = form(client)
    data["csrf"] = "bad"
    assert (
        client.post("/patients/1/assignments/preferences", data=data).status_code == 403
    )
    assert versions(client) == []


def test_replays_conflicts_optional_empty_and_clear(client):
    login(client, "patient1")
    assert post(client).status_code == 303
    assert post(client).status_code == 303
    assert len(versions(client)) == 1
    assert post(client, effects=("S072",), primary="S072").status_code == 409
    assert post(client, revision=1, effects=(), primary="").status_code == 303
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert page.context["tolerability_choices"] == []
    assert page.context["tolerability"]["comment"] == "Пожелание для обсуждения"


def test_legacy_intake_and_separate_form_share_last_sent_ordering(client):
    login(client, "patient1")
    assert legacy_preferences(client, ["S014"], "S014", submit=True).status_code == 303
    assert post(client, effects=("S055",), primary="S055").status_code == 303
    login(client, "doctor")
    assert client.get("/patients/1/assignments").context["tolerability"][
        "selected"
    ] == ["S055"]
    login(client, "patient1")
    assert legacy_preferences(client, ["S072"], "S072", submit=True).status_code == 303
    page = client.get("/patients/1/profile/assignments")
    assert page.context["medication_preferences"]["selected"] == ["S072"]
    login(client, "doctor")
    assert client.get("/patients/1/assignments").context["tolerability"][
        "selected"
    ] == ["S072"]
    login(client, "patient1")
    assert (
        post(client, revision=1, effects=("S055",), primary="S055").status_code == 303
    )
    login(client, "doctor")
    assert client.get("/patients/1/assignments").context["tolerability"][
        "selected"
    ] == ["S055"]


def test_preferences_only_backup_key_and_original_account_hashes_preserved(
    client, tmp_path
):
    login(client, "patient1")
    with connect(client.app.state.db_path) as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]
    post(client)
    target = tmp_path / "backup" / "preferences.db"
    backup(client.app.state.db_path, target)
    assert Path(str(target) + ".key").exists()
    with TestClient(create_app(target)) as restored:
        login(restored, "doctor")
        assert restored.get("/patients/1/assignments").context["tolerability"][
            "selected"
        ] == ["S014"]
    with connect(client.app.state.db_path) as c:
        assert [
            tuple(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")
        ] == before
    key = Path(client.app.state.db_path).parent / "attachments.key"
    old = key.with_suffix(".old")
    key.rename(old)
    try:
        assert client.get("/patients/1/profile/assignments").status_code == 503
        assert not key.exists()
    finally:
        old.rename(key)
