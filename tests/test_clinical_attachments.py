"""Only disposable files/keys, real encryption, permissions and scanner states."""

import json
from pathlib import Path
from app.services import clinical_attachments as service
from app.storage import connect
from tests.helpers import login, token
from tests.test_intake import form, send, save
from tests.test_meetings import audio
from tests.test_diary_questions import add
from tests.test_diary_cards import save_entry

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF"


def upload(client, content=PDF, name="synthetic.pdf", **changes):
    data = form(client, category="exams")
    data.update(changes)
    return client.post(
        "/patients/1/intake/files",
        data=data,
        files={"files": (name, content, "application/octet-stream")},
    )


def test_real_encryption_private_draft_access_and_published_file_review(
    client, monkeypatch
):
    monkeypatch.setattr(
        service, "scan", lambda content, suffix: ("clean", "Synthetic test scanner")
    )
    login(client, "patient1")
    assert upload(client).status_code == 200
    with connect(client.app.state.db_path) as c:
        row = c.execute("SELECT * FROM clinical_attachments").fetchone()
        assert row["content"] != PDF and b"%PDF-" not in row["content"]
        assert row["byte_size"] == len(PDF)
    assert client.get("/patients/1/clinical-files/1/content").content == PDF
    login(client, "doctor")
    assert client.get("/patients/1/clinical-files/1/content").status_code == 403
    login(client, "patient1")
    assert send(client).status_code == 200
    login(client, "doctor")
    assert client.get("/patients/1/clinical-files/1/content").content == PDF
    login(client, "patient2")
    assert client.get("/patients/1/clinical-files/1/content").status_code == 403
    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/clinical-files/1/delete", data=form(client, confirmed="yes")
        ).status_code
        == 409
    )
    key = Path(client.app.state.db_path).parent / "attachments.key"
    backup = key.with_suffix(".backup")
    key.rename(backup)
    try:
        assert client.get("/patients/1/clinical-files/1/content").status_code == 503
        assert not key.exists()
    finally:
        backup.rename(key)
    assert client.get("/patients/1/clinical-files/1/content").content == PDF


def test_unavailable_scan_is_not_claimed_clean_and_explicit_download_required(
    client, monkeypatch
):
    monkeypatch.setattr(service, "scan", lambda content, suffix: ("not_scanned", ""))
    login(client, "patient1")
    assert upload(client).status_code == 200
    assert (
        "Антивирусная проверка не выполнена"
        in client.get("/patients/1/clinical-files/1").text
    )
    assert client.get("/patients/1/clinical-files/1/content").status_code == 409
    assert (
        client.get(
            "/patients/1/clinical-files/1/content?download=true&ack_unscanned=true"
        ).content
        == PDF
    )
    assert (
        client.post(
            "/patients/1/clinical-files/1/delete", data=form(client, confirmed="yes")
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT content,deleted_at FROM clinical_attachments").fetchone()[
                0
            ]
            is None
        )


def test_infected_active_pdf_and_format_mismatch_do_not_create_rows(
    client, monkeypatch
):
    login(client, "patient1")
    assert upload(client, b"<script>test</script>", "renamed.pdf").status_code == 422
    assert upload(client, b"%PDF-1.4\n/JavaScript test\n%%EOF").status_code == 422
    monkeypatch.setattr(
        service, "scan", lambda content, suffix: ("infected", "Synthetic test scanner")
    )
    assert upload(client).status_code == 422
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM clinical_attachments").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM intake_drafts").fetchone()[0] == 0


def test_diary_voice_original_is_checked_saved_and_not_a_meeting_recording(
    client, monkeypatch
):
    monkeypatch.setattr(
        service, "scan", lambda content, suffix: ("clean", "Synthetic test scanner")
    )
    login(client, "doctor")
    assert add(client, kind="voice", title="Как прошёл день?").status_code == 200
    login(client, "patient1")
    from datetime import date

    response = client.post(
        "/patients/1/diary",
        data=dict(
            csrf=token(client),
            day=date.today().isoformat(),
            wellbeing="5",
            sleep="8",
            adherence="none",
            version_1="1",
        ),
        files={"files_1": ("voice.wav", audio(), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    assert "Открыть вложение #1" in response.text
    assert client.get("/patients/1/clinical-files/1/content").content == audio()
    login(client, "doctor")
    assert client.get("/patients/1/clinical-files/1/content").status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM meeting_recordings").fetchone()[0] == 0
        assert (
            c.execute("SELECT scope FROM clinical_attachments").fetchone()[0] == "diary"
        )


def test_forged_attachment_cannot_replace_a_numeric_diary_answer(client, monkeypatch):
    from datetime import date

    monkeypatch.setattr(
        service, "scan", lambda content, suffix: ("clean", "Synthetic test scanner")
    )
    login(client, "doctor")
    assert add(client, kind="number", title="Числовая метрика").status_code == 200
    login(client, "patient1")
    response = client.post(
        "/patients/1/diary",
        data=dict(
            csrf=token(client),
            day=date.today().isoformat(),
            wellbeing="5",
            sleep="8",
            adherence="none",
            version_1="1",
            metric_1="2",
        ),
        files={"files_1": ("report.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 422
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM clinical_attachments").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 0


def test_backup_preserves_key_and_reopens_encrypted_attachments(
    client, monkeypatch, tmp_path
):
    from scripts.backup import backup
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setattr(
        service, "scan", lambda content, suffix: ("clean", "Synthetic test scanner")
    )
    login(client, "patient1")
    assert upload(client).status_code == 200
    target = tmp_path / "restored" / "snapshot.db"
    backup(client.app.state.db_path, target)
    assert Path(str(target) + ".key").is_file()
    with TestClient(create_app(target)) as restored:
        login(restored, "patient1")
        assert restored.get("/patients/1/clinical-files/1/content").content == PDF
