"""Request-only key protection, immutable replies and consent-aware read access."""

import secrets
import sqlite3
import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData
from app.services import (
    extract_requests as requests,
    clinical_attachments as files,
    patient_extracts as documents,
)
from app.storage import connect
from tests.test_diary_library_offline import store
from tests.test_extract_requests import new_form


def test_request_only_key_loss_reply_history_and_read_after_consent_is_withdrawn(store):
    _, path, users = store
    with connect(path) as c:
        rid = requests.submit(c, path, users["p1"], 1, FormData(new_form()), [])
        form = FormData(
            dict(
                revision="1",
                client_token=secrets.token_urlsafe(24),
                status="clarify",
                reply="Уточните цель",
            )
        )
        requests.reply(c, path, users["d1"], 1, rid, form, [])
        requests.reply(c, path, users["d1"], 1, rid, form, [])
        assert len(requests.get(c, path, users["p1"], 1, rid)["events"]) == 1
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE extract_request_events SET status='ready'")
        c.execute(
            "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(1,'test',0,1,'later')"
        )
        assert requests.prefill(c, users["p1"], 1)["full_name"]
        assert requests.listing(c, path, users["p1"], 1)
        with pytest.raises(HTTPException) as denied:
            requests.submit(c, path, users["p1"], 1, FormData(new_form()), [])
        assert denied.value.status_code == 403
    key = files.key_path(path)
    original = key.read_bytes()
    key.unlink()
    try:
        with connect(path) as c:
            with pytest.raises(HTTPException) as lost:
                requests.get(c, path, users["p1"], 1, rid)
            assert lost.value.status_code == 503
        assert not key.exists()
    finally:
        key.write_bytes(original)


def test_education_institution_is_carried_to_document_and_request_identity_is_not_written_to_intake(
    store,
):
    _, path, users = store
    with connect(path) as c:
        rid = requests.submit(
            c,
            path,
            users["p1"],
            1,
            FormData(
                new_form(
                    purpose_other="Учебный отдел", full_name="Имя только для запроса"
                )
            ),
            [],
        )
        eid = requests.make_draft(
            c, path, users["d1"], 1, rid, FormData(dict(revision="1"))
        )
        data = documents.read(c, path, users["d1"], 1, eid, edit=True)["data"]
        assert data["recipient"] == "Для образовательного учреждения: Учебный отдел"
        assert data["identity"]["full_name"] == "Имя только для запроса"
        assert not data["reviewed"]
        assert c.execute("SELECT count(*) FROM intake_submissions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM intake_drafts").fetchone()[0] == 0
