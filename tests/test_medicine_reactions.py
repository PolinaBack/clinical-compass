"""Fictional version-bound reports, explicit publication, privacy, roles and backup."""

import secrets
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from app.services import medicine_reactions as service
from tests.helpers import login, token
from tests.test_medicine_workspace import scheme, saved
from scripts.backup import backup


def setup_scheme(client, codes=("D01",)):
    login(client, "doctor")
    assert (
        client.post(
            "/patients/1/assignments/medicines", data=scheme(client, codes=codes)
        ).status_code
        == 200
    )


def share(client, revision=1, pid=1, **extra):
    return client.post(
        f"/patients/{pid}/assignments/medicines/share",
        data={"csrf": token(client), "revision": revision, **extra},
        follow_redirects=False,
    )


def report_form(client, **changes):
    with connect(client.app.state.db_path) as c:
        draft = c.execute(
            "SELECT id FROM medicine_drafts WHERE patient_id=1 ORDER BY id LIMIT 1"
        ).fetchone()
    data = dict(
        csrf=token(client),
        draft_id=draft["id"],
        revision=1,
        code="D01",
        client_token=secrets.token_urlsafe(24),
        symptom="other",
        custom_symptom="ВЫМЫШЛЕННАЯ РЕАКЦИЯ",
        onset="2026-10-01",
        impact="moderate",
        state="ongoing",
        description="СИНТЕТИЧЕСКОЕ ОПИСАНИЕ",
        return_to="/patients/1/profile/assignments",
    )
    data.update(changes)
    return data


def report(client, pid=1, **changes):
    return client.post(
        f"/patients/{pid}/assignments/medicines/reactions",
        data=report_form(client, **changes),
        follow_redirects=False,
    )


def review(client, **changes):
    data = dict(
        csrf=token(client),
        report_id=1,
        client_token=secrets.token_urlsafe(24),
        relation="possible",
        assessment_note="ВЫМЫШЛЕННАЯ ОЦЕНКА ВРАЧА",
        patient_visible="yes",
    )
    data.update(changes)
    return client.post(
        "/patients/1/assignments/medicines/reactions/review",
        data=data,
        follow_redirects=False,
    )


def counts(client):
    with connect(client.app.state.db_path) as c:
        return tuple(
            c.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in (
                "medicine_plan_shares",
                "medicine_reaction_reports",
                "medicine_reaction_reviews",
            )
        )


def test_only_explicit_share_exposes_public_saved_fields_and_is_idempotent(client):
    setup_scheme(client)
    login(client, "patient1")
    assert (
        client.get("/patients/1/profile/assignments").context["shared_medicine_plans"]
        == []
    )
    assert report(client).status_code == 403
    assert share(client).status_code == 403
    login(client, "doctor")
    assert share(client, csrf="bad").status_code == 403
    assert share(client, revision=9).status_code == 409
    for _ in range(2):
        assert share(client).status_code == 303
    assert counts(client) == (1, 0, 0)
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    src = page.context["shared_medicine_plans"][0]["items"][0]
    assert src["item"]["dose"] == "СИНТЕТИЧЕСКАЯ ДОЗА"
    assert "preference_matches" not in src["item"] and "tolerability" not in src["item"]
    assert "Сообщить о побочном эффекте" in page.text
    assert 'data-shared-medicine="D01"' in page.text


def test_patient_report_is_encrypted_version_bound_and_in_doctor_activity(client):
    setup_scheme(client)
    share(client)
    login(client, "patient1")
    assert report(client).status_code == 303
    with connect(client.app.state.db_path) as c:
        row = c.execute("SELECT * FROM medicine_reaction_reports").fetchone()
        assert row["author_role"] == "patient" and row["patient_visible"] == 1
        assert (
            b"other" not in row["payload"]
            and "ВЫМЫШЛЕННАЯ".encode() not in row["payload"]
        )
    login(client, "doctor")
    page = client.get("/patients/1/assignments")
    assert (
        page.context["reaction_reports"][0]["medicine"]["dose"] == "СИНТЕТИЧЕСКАЯ ДОЗА"
    )
    assert "СИНТЕТИЧЕСКОЕ ОПИСАНИЕ" in page.text
    assert "Сообщено о возможной реакции на лекарство" in client.get("/doctor").text
    assert saved(client)["revision"] == 1
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert (
            restarted.get("/patients/1/profile/assignments").context[
                "reaction_reports"
            ][0]["label"]
            == "ВЫМЫШЛЕННАЯ РЕАКЦИЯ"
        )


def test_doctor_can_record_privately_then_share_scheme_without_exposing_private_note(
    client,
):
    setup_scheme(client)
    assert (
        report(
            client, relation="doctor_attributed", assessment_note="ОСНОВАНИЕ ВРАЧА"
        ).status_code
        == 303
    )
    assert share(client).status_code == 303
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert page.context["reaction_reports"] == []
    assert "ОСНОВАНИЕ ВРАЧА" not in page.text
    assert "ВЫМЫШЛЕННАЯ РЕАКЦИЯ" not in page.text
    login(client, "doctor")
    assert (
        report(
            client,
            patient_visible="yes",
            relation="possible",
            assessment_note="ОТКРЫТАЯ ОЦЕНКА",
        ).status_code
        == 303
    )
    login(client, "patient1")
    assert "ОТКРЫТАЯ ОЦЕНКА" in client.get("/patients/1/profile/assignments").text


def test_clinician_reviews_are_explicit_and_private_by_default(client):
    setup_scheme(client)
    share(client)
    login(client, "patient1")
    report(client)
    assert review(client).status_code == 403
    login(client, "doctor")
    assert review(client, patient_visible="").status_code == 303
    login(client, "patient1")
    assert (
        "ВЫМЫШЛЕННАЯ ОЦЕНКА ВРАЧА"
        not in client.get("/patients/1/profile/assignments").text
    )
    login(client, "doctor")
    assert review(client).status_code == 303
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert len(page.context["reaction_reports"][0]["reviews"]) == 1
    assert "ВЫМЫШЛЕННАЯ ОЦЕНКА ВРАЧА" in page.text
    assert counts(client) == (1, 1, 2)


@pytest.mark.parametrize(
    "changes",
    [
        dict(code="D02"),
        dict(revision=99),
        dict(draft_id=9223372036854775808),
        dict(symptom="unknown"),
        dict(custom_symptom=""),
        dict(onset="2999-01-01"),
        dict(impact="invalid"),
        dict(state="invalid"),
        dict(relation="doctor_attributed", assessment_note="spoofed"),
        dict(client_token="bad"),
    ],
)
def test_invalid_or_spoofed_patient_reports_do_not_save(client, changes):
    setup_scheme(client)
    share(client)
    login(client, "patient1")
    assert report(client, **changes).status_code in (403, 422)
    assert counts(client) == (1, 0, 0)


def test_registry_reaction_and_custom_reaction_csrf_consent_and_other_patient(client):
    setup_scheme(client)
    share(client)
    with connect(client.app.state.db_path) as c:
        key = next(
            r["key"]
            for r in service.records(c)
            if r["drug"] == "D01" and r["medical"] == "тошнота"
        )
    login(client, "patient1", consent=False)
    assert report(client, symptom=key).status_code == 403
    login(client, "patient1")
    assert report(client, symptom=key, csrf="bad").status_code == 403
    assert report(client, symptom=key).status_code == 303
    login(client, "patient2")
    assert report(client).status_code == 403
    assert report(client, pid=2).status_code == 404
    assert (
        client.get("/patients/2/profile/assignments").context["reaction_reports"] == []
    )


def test_double_submit_conflict_and_source_version_not_overwritten_by_later_scheme(
    client,
):
    setup_scheme(client)
    share(client)
    login(client, "patient1")
    data = report_form(client)
    for _ in range(2):
        assert (
            client.post(
                "/patients/1/assignments/medicines/reactions", data=data
            ).status_code
            == 200
        )
    assert counts(client) == (1, 1, 0)
    data["description"] = "Changed"
    assert (
        client.post(
            "/patients/1/assignments/medicines/reactions", data=data
        ).status_code
        == 409
    )
    login(client, "doctor")
    updated = scheme(client, revision=1, codes=("D11",))
    assert (
        client.post("/patients/1/assignments/medicines", data=updated).status_code
        == 200
    )
    assert share(client, revision=2).status_code == 303
    page = client.get("/patients/1/assignments")
    assert page.context["reaction_reports"][0]["code"] == "D01"
    assert page.context["reaction_reports"][0]["revision"] == 1
    assert (
        page.context["reaction_reports"][0]["medicine"]["dose"] == "СИНТЕТИЧЕСКАЯ ДОЗА"
    )
    assert "Реакции по ранее сохранённым препаратам" in page.text
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert len(page.context["shared_medicine_plans"]) == 2
    assert page.context["shared_medicine_plans"][0]["items"][0]["code"] == "D11"
    assert (
        report(client).status_code == 303
    )  # still can report against the explicitly shared older version


def test_physician_assessment_needs_reason_and_private_record_cannot_be_shown_without_share(
    client,
):
    setup_scheme(client)
    assert report(client, relation="possible").status_code == 422
    assert report(client, patient_visible="yes").status_code == 422
    assert counts(client) == (0, 0, 0)
    assert report(client).status_code == 303
    assert review(client, patient_visible="").status_code == 303
    assert review(client).status_code == 422
    assert counts(client) == (0, 1, 1)


def test_backup_immutable_records_lost_key_and_accounts_preserved(client, tmp_path):
    setup_scheme(client)
    share(client)
    with connect(client.app.state.db_path) as c:
        original = [tuple(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]
    login(client, "patient1")
    report(client)
    destination = tmp_path / "backup" / "reactions.db"
    backup(client.app.state.db_path, destination)
    assert Path(str(destination) + ".key").is_file()
    with TestClient(create_app(destination)) as restarted:
        login(restarted, "patient1")
        assert (
            len(
                restarted.get("/patients/1/profile/assignments").context[
                    "reaction_reports"
                ]
            )
            == 1
        )
    import sqlite3

    with connect(client.app.state.db_path) as c:
        for sql in [
            "UPDATE medicine_reaction_reports SET patient_visible=0",
            "DELETE FROM medicine_reaction_reports",
            "DELETE FROM medicine_plan_shares",
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(sql)
        assert [
            tuple(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")
        ] == original
    key = Path(client.app.state.db_path).parent / "attachments.key"
    old = key.with_suffix(".old")
    key.rename(old)
    try:
        assert client.get("/patients/1/profile/assignments").status_code == 503
        assert not key.exists()
    finally:
        old.rename(key)
