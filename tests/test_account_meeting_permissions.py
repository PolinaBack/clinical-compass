"""One patient decision covers meetings; doctors have no consent forms or self-grants."""

import re
from datetime import datetime, timezone

import pytest

from app.storage import connect
from app.services import meetings as service
from app.services.consent import (
    account_context,
    patient_media_permissions,
    notifications_allowed,
    create_consultation_context,
    require_processing_consent,
)
from tests.helpers import login, token
from tests.test_settings import save_section, user_row
from tests.test_meetings import meeting, upload, recording_id, queue, SyntheticModels


def form_data(client, section):
    html = client.get("/settings").text
    form = html.split(f'action="/settings/{section}"', 1)[1].split("</form>", 1)[0]
    data = dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form)
    )
    return data, form


def enable(client):
    login(client, "patient1")
    assert (
        save_section(
            client,
            "consultations",
            audio="true",
            video="true",
            transcription="true",
            summary="true",
        ).status_code
        == 200
    )
    login(client, "doctor")


def test_doctor_has_no_consent_settings_and_cannot_grant_them(client):
    login(client, "doctor")
    html = client.get("/settings").text
    assert 'action="/settings/consultations"' not in html
    assert 'action="/settings/consents/' not in html
    for section in ("profile", "notifications", "interface"):
        assert f'action="/settings/{section}"' in html
    assert (
        client.post(
            "/settings/consultations",
            data={"csrf": token(client), "revision": "0", "audio": "true"},
        ).status_code
        == 403
    )
    with connect(client.app.state.db_path) as c:
        from app.services.consent import documents

        document = documents(c)["notifications"]["id"]
    assert (
        client.post(
            "/settings/consents/notifications",
            data={
                "csrf": token(client),
                "document_id": document,
                "expected_id": "0",
                "action": "granted",
                "confirmed": "true",
            },
        ).status_code
        == 403
    )


def test_patient_has_one_four_toggle_form_and_reading_does_not_grant(client, meeting):
    login(client, "patient1")
    _, form = form_data(client, "consultations")
    assert form.count('type="checkbox"') == 4
    assert "checked" not in form
    page = client.get(f"/meetings/{meeting[0]}").text
    assert 'action="/meetings/' not in page
    assert "/settings#consultations" in page
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM account_consent_contexts").fetchone()[0]
            == 0
        )
        assert c.execute("SELECT COUNT(*) FROM consent_events").fetchone()[0] == 0


def test_one_save_allows_two_meetings_without_doctor_or_second_patient_decision(
    client, meeting
):
    mid, _, vid = meeting
    enable(client)
    assert upload(client, mid).status_code == 303
    with connect(client.app.state.db_path) as c:
        second = c.execute(
            """INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at)
          SELECT patient_id,author_id,'2026-10-23T15:00',timezone_label,url,fallback,created_at FROM appointments WHERE id=?""",
            (vid,),
        ).lastrowid
    response = client.post(
        f"/patients/1/appointments/{second}/meeting",
        data={"csrf": token(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        upload(client, int(response.headers["location"].split("/")[-1])).status_code
        == 303
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM account_consent_contexts").fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM consent_events").fetchone()[0] == 4
        assert not c.execute(
            "SELECT e.id FROM consent_events e JOIN accounts a ON a.id=e.account_id WHERE a.role='doctor'"
        ).fetchall()


def test_disabling_permission_cancels_queued_job_immediately_without_deleting_file(
    client, meeting
):
    enable(client)
    assert upload(client, meeting[0]).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303
    login(client, "patient1")
    assert save_section(client, "consultations", transcription="").status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT status,error_code FROM meeting_jobs").fetchone()[
            :
        ] == ("cancelled", "permission_changed")
        assert (
            c.execute("SELECT length(content) FROM meeting_recordings").fetchone()[0]
            > 0
        )
    assert not service.process_one(client.app.state.db_path, SyntheticModels())
    login(client, "doctor")
    assert queue(client, rid, "transcript").status_code == 403


@pytest.mark.parametrize("regrant", [False, True])
def test_permission_changed_during_model_call_discards_result_even_after_regrant(
    client, meeting, regrant
):
    enable(client)
    assert upload(client, meeting[0]).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303

    class Changed(SyntheticModels):
        def transcribe(self, path, checkpoint):
            login(client, "patient1")
            assert (
                save_section(client, "consultations", transcription="").status_code
                == 200
            )
            if regrant:
                assert (
                    save_section(
                        client, "consultations", transcription="true"
                    ).status_code
                    == 200
                )
            return super().transcribe(path, lambda: None)

    assert service.process_one(client.app.state.db_path, Changed())
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT status FROM meeting_jobs").fetchone()[0] == "cancelled"
        assert not c.execute("SELECT id FROM meeting_artifacts").fetchall()


def test_stale_single_form_cannot_restore_withdrawn_permission(client):
    login(client, "patient1")
    assert save_section(client, "consultations", audio="true").status_code == 200
    stale, _ = form_data(client, "consultations")
    stale["audio"] = "true"
    assert save_section(client, "consultations", audio="").status_code == 200
    assert client.post("/settings/consultations", data=stale).status_code == 409
    with connect(client.app.state.db_path) as c:
        assert not patient_media_permissions(c, 1)["audio_recording"]


def test_invalid_document_rolls_back_all_four_decisions_and_preferences(client):
    login(client, "patient1")
    data, _ = form_data(client, "consultations")
    data.update(
        audio="true",
        video="true",
        transcription="true",
        summary="true",
        document_id_ai_summary="99999",
    )
    assert client.post("/settings/consultations", data=data).status_code == 409
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM consent_events").fetchone()[0] == 0
        assert (
            c.execute("SELECT COUNT(*) FROM consultation_preferences").fetchone()[0]
            == 0
        )
        assert (
            c.execute("SELECT COUNT(*) FROM account_consent_contexts").fetchone()[0]
            == 0
        )


def test_old_meeting_decisions_and_preference_flags_are_not_new_global_grants(
    client, meeting
):
    with connect(client.app.state.db_path) as c:
        old = c.execute(
            "SELECT id FROM consent_documents WHERE purpose='audio_recording' AND version='meeting-local-v2-2026-10-21'"
        ).fetchone()[0]
        for row in c.execute(
            "SELECT account_id FROM consent_participants WHERE context_id=?",
            (meeting[1],),
        ).fetchall():
            c.execute(
                "INSERT INTO consent_events(account_id,context_id,purpose,document_id,status,created_at) VALUES(?,?,'audio_recording',?,'granted','2026-10-21')",
                (row[0], meeting[1], old),
            )
        patient = user_row(client, "patient1")
        c.execute(
            "INSERT INTO consultation_preferences VALUES(?,1,1,1,1,1,'2026-10-21')",
            (patient["id"],),
        )
        assert not patient_media_permissions(c, 1)["audio_recording"]
    assert upload(client, meeting[0]).status_code == 403
    login(client, "patient1")
    assert save_section(client, "consultations", audio="true").status_code == 200
    login(client, "doctor")
    assert upload(client, meeting[0]).status_code == 303


def test_patient_permission_never_applies_to_another_patient(client):
    enable(client)
    with connect(client.app.state.db_path) as c:
        assert all(patient_media_permissions(c, 1).values())
        assert not any(patient_media_permissions(c, 2).values())
        context = create_consultation_context(c, 2, user_row(client, "doctor")["id"])
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            require_processing_consent(c, context, "audio_recording")


def test_notifications_permission_is_in_the_same_single_preferences_form(client):
    login(client, "patient1")
    hidden, _ = form_data(client, "notifications")
    assert (
        save_section(
            client, "notifications", **hidden, enabled="true", diary="true"
        ).status_code
        == 200
    )
    uid = user_row(client, "patient1")["id"]
    noon = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
    with connect(client.app.state.db_path) as c:
        assert notifications_allowed(c, uid, "diary", noon)
    hidden, _ = form_data(client, "notifications")
    assert (
        save_section(
            client, "notifications", **hidden, enabled="", diary="true"
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        assert not notifications_allowed(c, uid, "diary", noon)


def test_closed_account_permission_scope_cannot_authorize_processing(client):
    enable(client)
    with connect(client.app.state.db_path) as c:
        context = account_context(c, user_row(client, "patient1")["id"])
        c.execute(
            "UPDATE consultation_consent_contexts SET closed_at='2026-10-22' WHERE id=?",
            (context,),
        )
        assert not any(patient_media_permissions(c, 1).values())


def test_doctor_notification_preferences_need_no_own_consent_form(client):
    login(client, "doctor")
    assert (
        client.post(
            "/settings/notifications",
            data={
                "csrf": token(client),
                "revision": "0",
                "diary": "true",
                "preferred_time": "20:00",
                "quiet_start": "22:00",
                "quiet_end": "08:00",
            },
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        uid = user_row(client, "doctor")["id"]
        assert notifications_allowed(
            c, uid, "diary", datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
        )
        assert not c.execute(
            "SELECT id FROM consent_events WHERE account_id=?", (uid,)
        ).fetchall()
