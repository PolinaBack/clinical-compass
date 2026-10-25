"""E01 acceptance: persistence, scoped consent, revocation and old-data safety."""

from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import connect
from app.services.account_settings import read_settings
from app.services.consent import (
    MEDIA_PURPOSES,
    create_consultation_context,
    documents,
    latest_event,
    require_processing_consent,
    notifications_allowed,
    account_context,
)
from tests.helpers import login, token


def user_row(client, username):
    with connect(client.app.state.db_path) as c:
        return c.execute(
            "SELECT * FROM accounts WHERE username=?", (username,)
        ).fetchone()


def preferences(client):
    with connect(client.app.state.db_path) as c:
        return read_settings(c, user_row(client, "patient1"))


def save_section(client, section, **values):
    data = dict(preferences(client)[section])
    data = {
        k: ("true" if v else "") if isinstance(v, int) and k != "revision" else v
        for k, v in data.items()
        if k not in ("account_id", "updated_at")
    }
    data.update(values)
    if section == "consultations":
        with connect(client.app.state.db_path) as c:
            user = user_row(client, "patient1")
            context = account_context(c, user["id"])
            for purpose in MEDIA_PURPOSES:
                previous = (
                    latest_event(c, user["id"], purpose, context) if context else None
                )
                data[f"document_id_{purpose}"] = documents(c)[purpose]["id"]
                data[f"expected_id_{purpose}"] = previous["id"] if previous else 0
    data["csrf"] = token(client, "/settings")
    return client.post("/settings/" + section, data=data)


def decision_data(client, username, purpose, context_id=None, action="granted"):
    with connect(client.app.state.db_path) as c:
        user = user_row(client, username)
        lookup_context = context_id or (
            account_context(c, user["id"]) if purpose in MEDIA_PURPOSES else None
        )
        previous = latest_event(c, user["id"], purpose, lookup_context)
        return dict(
            csrf=token(client, "/settings"),
            document_id=documents(c)[purpose]["id"],
            expected_id=previous["id"] if previous else 0,
            context_id=context_id or "",
            action=action,
            confirmed="true",
        )


def decision(client, username, purpose, context_id=None, action="granted"):
    return client.post(
        "/settings/consents/" + purpose,
        data=decision_data(client, username, purpose, context_id, action),
    )


def new_context(client):
    with connect(client.app.state.db_path) as c:
        return create_consultation_context(c, 1, user_row(client, "doctor")["id"])


def allowed(client, context_id, purpose):
    with connect(client.app.state.db_path) as c:
        try:
            require_processing_consent(c, context_id, purpose)
            return True
        except HTTPException as exc:
            assert exc.status_code == 403
            return False


def test_optional_defaults_and_account_preferences_do_not_grant_consent(client):
    login(client, "patient1")
    settings = preferences(client)
    assert not any(
        settings["notifications"][k] for k in ("diary", "medication", "tasks")
    )
    assert not any(
        settings["consultations"][k]
        for k in ("audio", "video", "transcription", "summary")
    )
    # Legacy preference flags alone must not become the new global permission.
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO consultation_preferences VALUES(?,1,1,1,1,1,'2026-10-21')",
            (user_row(client, "patient1")["id"],),
        )
    context_id = new_context(client)
    assert all(not allowed(client, context_id, p) for p in MEDIA_PURPOSES)
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM consent_events").fetchone()[0] == 0
    page = client.get("/settings")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert all(
        title in page.text
        for title in ("Профиль", "Уведомления", "Конфиденциальность", "Записи встреч")
    )


def test_profile_and_all_preferences_persist_after_real_app_restart(client):
    login(client, "patient1")
    assert (
        save_section(
            client,
            "profile",
            display_name="Своё имя",
            timezone="Asia/Vladivostok",
            address_style="friendly",
        ).status_code
        == 200
    )
    assert (
        save_section(
            client,
            "notifications",
            diary="true",
            tasks="true",
            preferred_time="18:35",
            quiet_start="23:15",
            quiet_end="07:45",
        ).status_code
        == 200
    )
    assert (
        save_section(client, "consultations", video="true", summary="true").status_code
        == 200
    )
    path = client.app.state.db_path
    with TestClient(create_app(path)) as restarted:
        login(restarted, "patient1", consent=False)
        state = preferences(restarted)
        assert state["profile"]["display_name"] == "Своё имя"
        assert state["profile"]["timezone"] == "Asia/Vladivostok"
        assert state["profile"]["address_style"] == "friendly"
        assert state["notifications"]["diary"] == state["notifications"]["tasks"] == 1
        assert state["notifications"]["medication"] == 0
        assert state["notifications"]["preferred_time"] == "18:35"
        assert state["notifications"]["quiet_start"] == "23:15"
        assert state["consultations"]["video"] == state["consultations"]["summary"] == 1
        with connect(restarted.app.state.db_path) as c:
            from app.services.consent import patient_media_permissions

            permissions = patient_media_permissions(c, 1)
            assert permissions["video_recording"] and permissions["ai_summary"]
            assert (
                not permissions["audio_recording"]
                and not permissions["ai_transcription"]
            )
        assert "Своё имя" in restarted.get("/settings").text
        assert restarted.get("/patients/1").status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        dict(display_name=" "),
        dict(display_name="x" * 81),
        dict(timezone="Not/AZone"),
        dict(address_style="other"),
    ],
)
def test_invalid_profile_does_not_replace_existing_settings(client, overrides):
    login(client, "patient1")
    assert (
        save_section(client, "profile", display_name="Сохранённое имя").status_code
        == 200
    )
    assert save_section(client, "profile", **overrides).status_code == 422
    assert preferences(client)["profile"]["display_name"] == "Сохранённое имя"
    assert preferences(client)["profile"]["revision"] == 1


def test_notification_consent_categories_timezone_and_revocation(client):
    login(client, "patient1")
    aid = user_row(client, "patient1")["id"]
    assert save_section(client, "notifications", diary="true").status_code == 200
    noon = datetime(2026, 10, 21, 9, tzinfo=timezone.utc)  # 12:00 Moscow
    night = datetime(2026, 10, 21, 20, tzinfo=timezone.utc)  # 23:00 Moscow
    with connect(client.app.state.db_path) as c:
        assert not notifications_allowed(c, aid, "diary", noon)
    assert decision(client, "patient1", "notifications").status_code == 200
    with connect(client.app.state.db_path) as c:
        assert notifications_allowed(c, aid, "diary", noon)
        assert not notifications_allowed(c, aid, "medication", noon)
        assert not notifications_allowed(c, aid, "diary", night)
    assert (
        decision(client, "patient1", "notifications", action="withdrawn").status_code
        == 200
    )
    assert preferences(client)["notifications"]["diary"] == 1
    with connect(client.app.state.db_path) as c:
        assert not notifications_allowed(c, aid, "diary", noon)
        assert [
            r[0] for r in c.execute("SELECT status FROM consent_events ORDER BY id")
        ] == ["granted", "withdrawn"]


@pytest.mark.parametrize(
    "overrides",
    [
        dict(preferred_time="24:00"),
        dict(quiet_start="wrong"),
        dict(quiet_start="22:00", quiet_end="22:00"),
        dict(diary="yes"),
    ],
)
def test_invalid_notification_settings_are_atomic(client, overrides):
    login(client, "patient1")
    assert save_section(client, "notifications", **overrides).status_code == 422
    assert preferences(client)["notifications"]["revision"] == 0


@pytest.mark.parametrize("purpose", MEDIA_PURPOSES)
def test_patient_permission_covers_all_meetings_and_revocation_stops_it(
    client, purpose
):
    login(client, "patient1")
    context_id = new_context(client)
    assert decision(client, "patient1", purpose).status_code == 200
    assert allowed(client, context_id, purpose)
    login(client, "doctor")
    assert decision(client, "doctor", purpose, context_id).status_code == 403
    assert allowed(client, context_id, purpose)
    assert all(
        not allowed(client, context_id, other)
        for other in MEDIA_PURPOSES
        if other != purpose
    )
    other_context = new_context(client)
    assert allowed(client, other_context, purpose)
    login(client, "patient1")
    assert decision(client, "patient1", purpose, action="withdrawn").status_code == 200
    assert not allowed(client, context_id, purpose)
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        assert not allowed(restarted, context_id, purpose)
        login(restarted, "patient1", consent=False)
        assert "Согласие отозвано" in restarted.get("/settings").text


def test_csrf_foreign_access_and_fake_context_cannot_change_consents(client):
    assert client.get("/settings").status_code == 401
    login(client, "patient1")
    context_id = new_context(client)
    data = decision_data(client, "patient1", "audio_recording", context_id)
    assert (
        client.post(
            "/settings/consents/audio_recording", data={**data, "csrf": "bad"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/settings/consents/audio_recording", data={**data, "confirmed": ""}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/settings/consents/audio_recording",
            data={
                **decision_data(client, "patient1", "audio_recording"),
                "confirmed": "",
            },
        ).status_code
        == 422
    )
    assert (
        decision(client, "patient1", "audio_recording", "invented-context").status_code
        == 403
    )
    login(client, "patient2")
    assert context_id not in client.get("/settings").text
    assert (
        decision(client, "patient2", "audio_recording", context_id).status_code == 403
    )
    assert client.post("/settings/profile", data={"csrf": "bad"}).status_code == 403
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM consent_events").fetchone()[0] == 0


def test_stale_forms_cannot_regrant_after_withdrawal_or_overwrite_profile(client):
    login(client, "patient1")
    assert decision(client, "patient1", "notifications").status_code == 200
    stale = decision_data(client, "patient1", "notifications")
    assert (
        decision(client, "patient1", "notifications", action="withdrawn").status_code
        == 200
    )
    assert (
        client.post("/settings/consents/notifications", data=stale).status_code == 409
    )
    assert save_section(client, "profile", display_name="Новое").status_code == 200
    assert (
        save_section(client, "profile", display_name="Старое", revision="0").status_code
        == 409
    )
    assert preferences(client)["profile"]["display_name"] == "Новое"


def test_document_versions_and_append_only_history(client):
    login(client, "patient1")
    assert decision(client, "patient1", "notifications").status_code == 200
    with connect(client.app.state.db_path) as c:
        old = documents(c)["notifications"]
        c.execute(
            """INSERT INTO consent_documents(purpose,version,title,body,retention,created_at)
               VALUES('notifications','v2-test','Обновлённое','Новая цель','Новые условия','2026-10-21')"""
        )
    with connect(client.app.state.db_path) as c:
        assert not notifications_allowed(c, user_row(client, "patient1")["id"], "diary")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE consent_events SET status='withdrawn'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM consent_events")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "UPDATE consent_documents SET body='replacement' WHERE id=?",
                (old["id"],),
            )
    assert old["body"] in client.get("/settings").text
    assert decision(client, "patient1", "notifications").status_code == 200


def test_withdrawal_remains_possible_after_closing_or_changing_clinician(client):
    login(client, "patient1")
    context_id = new_context(client)
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO consent_events(account_id,context_id,purpose,document_id,status,created_at) VALUES(?,?,'audio_recording',?,'granted','2026-10-21')",
            (
                user_row(client, "patient1")["id"],
                context_id,
                documents(c)["audio_recording"]["id"],
            ),
        )
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой врач')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=1")
        c.execute(
            "UPDATE consultation_consent_contexts SET closed_at='2026-10-21' WHERE id=?",
            (context_id,),
        )
    assert not allowed(client, context_id, "audio_recording")
    assert (
        decision(
            client, "patient1", "audio_recording", context_id, "withdrawn"
        ).status_code
        == 200
    )
    assert (
        decision(client, "patient1", "audio_recording", context_id).status_code == 409
    )


def test_participation_withdrawal_blocks_otherwise_granted_processing(client):
    login(client, "patient1")
    context_id = new_context(client)
    assert decision(client, "patient1", "audio_recording").status_code == 200
    login(client, "doctor")
    assert decision(client, "doctor", "audio_recording", context_id).status_code == 403
    assert allowed(client, context_id, "audio_recording")
    login(client, "patient1")
    assert client.post("/consent", data={"csrf": token(client)}).status_code == 200
    assert not allowed(client, context_id, "audio_recording")
    # Joining again must not restore an earlier optional permission.
    assert (
        client.post(
            "/consent",
            data={
                "csrf": token(client),
                "accepted": "true",
                "adult": "true",
            },
        ).status_code
        == 200
    )
    assert not allowed(client, context_id, "audio_recording")
    assert client.get("/settings").status_code == 200


def test_missing_clinician_binding_never_bypasses_context_guard(client):
    login(client, "patient1")
    context_id = new_context(client)
    assert decision(client, "patient1", "audio_recording").status_code == 200
    login(client, "doctor")
    assert decision(client, "doctor", "audio_recording", context_id).status_code == 403
    assert allowed(client, context_id, "audio_recording")
    with connect(client.app.state.db_path) as c:
        c.execute("UPDATE accounts SET doctor_id=NULL WHERE username='doctor'")
    assert not allowed(client, context_id, "audio_recording")
    assert (
        decision(
            client, "doctor", "audio_recording", context_id, "withdrawn"
        ).status_code
        == 403
    )


def test_doctor_settings_are_separate_and_do_not_reassign_patients(client):
    login(client, "doctor")
    page = client.get("/settings")
    assert "Специалист" in page.text
    assert (
        client.post(
            "/settings/profile",
            data={
                "csrf": token(client),
                "revision": "0",
                "display_name": "Имя врача",
                "timezone": "UTC",
                "address_style": "polite",
                "role": "patient",
                "patient_id": "2",
            },
        ).status_code
        == 200
    )
    assert client.get("/doctor").status_code == 200
    login(client, "patient1")
    assert "Имя врача" not in client.get("/settings").text
    assert preferences(client)["profile"]["revision"] == 0


def test_migration_checksum_and_original_records_survive_restarts(client):
    login(client, "patient1")
    original = user_row(client, "patient1")
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        with connect(restarted.app.state.db_path) as c:
            assert [
                r[0]
                for r in c.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ] == [
                "001_account_settings.sql",
                "002_interface_preferences.sql",
                "003_diary_cards.sql",
                "004_drug_library.sql",
                "005_meeting_processing.sql",
                "006_meeting_progress.sql",
                "007_account_meeting_consents.sql",
                "008_intake_and_diary_questions.sql",
                "009_resources_and_family.sql",
                "010_consultation_slots.sql",
                "011_patient_ai.sql",
                "012_clinician_forum.sql",
                "013_patient_information_requests.sql",
                "014_medicine_drafts.sql",
                "015_care_orders.sql",
                "016_medication_preferences.sql",
                "017_medicine_reactions.sql",
            ]
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            user_row(restarted, "patient1")["password_hash"]
            == original["password_hash"]
        )
    with connect(client.app.state.db_path) as c:
        c.execute("UPDATE schema_migrations SET checksum='changed'")
    with pytest.raises(RuntimeError, match="Applied migration changed"):
        with TestClient(create_app(client.app.state.db_path)):
            pass
