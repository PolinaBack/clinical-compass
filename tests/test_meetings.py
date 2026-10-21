"""Synthetic media/AI fixtures test workflow, never clinical model accuracy."""

import io
import json
import sqlite3
import wave
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect, now
from app.services import meetings as service
from app.services.consent import documents
from app.services.meeting_ai import (
    ProcessingError,
    validate_summary,
    validate_transcript,
    configuration,
    chunks,
)
from tests.helpers import login, token


def test_appointment_without_booking_works_from_html_form(client):
    login(client, "doctor")
    r = client.post(
        "/patients/1/appointments",
        data={
            "csrf": token(client),
            "booking_id": "",
            "starts_at": "2026-10-21T15:00",
            "timezone_label": "Москва",
            "url": "https://example.org/meeting",
            "fallback": "Согласованный резерв",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM appointments").fetchone()[0] == 1


def audio():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000)
    return stream.getvalue()


@pytest.fixture
def meeting(client):
    login(client, "patient1")
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        uid = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[0]
        vid = c.execute(
            "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                1,
                uid,
                "2026-10-21T15:00",
                "Москва",
                "https://example.org/meeting",
                "Согласованный резерв",
                now(),
            ),
        ).lastrowid
    r = client.post(
        f"/patients/1/appointments/{vid}/meeting",
        data={"csrf": token(client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    mid = int(r.headers["location"].split("/")[-1])
    assert client.get(f"/meetings/{mid}").status_code == 200
    with connect(client.app.state.db_path) as c:
        context = c.execute(
            "SELECT context_id FROM meeting_sessions WHERE id=?", (mid,)
        ).fetchone()[0]
    return mid, context, vid


def grants(
    client,
    context,
    purposes=("audio_recording", "ai_transcription", "ai_summary"),
    status="granted",
    who=None,
):
    with connect(client.app.state.db_path) as c:
        docs = documents(c)
        rows = c.execute(
            "SELECT account_id FROM consent_participants WHERE context_id=?", (context,)
        ).fetchall()
        for a in rows:
            if who is not None and a[0] != who:
                continue
            for purpose in purposes:
                c.execute(
                    "INSERT INTO consent_events(account_id,context_id,purpose,document_id,status,created_at) VALUES(?,?,?,?,?,?)",
                    (a[0], context, purpose, docs[purpose]["id"], status, now()),
                )


def upload(client, mid, content=None, **data):
    return client.post(
        f"/meetings/{mid}/recordings",
        data={"csrf": token(client), "media_kind": "audio", **data},
        files={
            "file": (
                "synthetic.wav",
                audio() if content is None else content,
                "audio/wav",
            )
        },
        follow_redirects=False,
    )


def recording_id(client):
    with connect(client.app.state.db_path) as c:
        return c.execute("SELECT MAX(id) FROM meeting_recordings").fetchone()[0]


def queue(client, rid, kind):
    return client.post(
        f"/meeting-recordings/{rid}/jobs/{kind}",
        data={"csrf": token(client)},
        follow_redirects=False,
    )


class SyntheticModels:
    def transcribe(self, path, checkpoint):
        assert path.is_file()
        checkpoint()
        return [
            dict(start=0, end=0.45, text="Синтетический разговор о сне."),
            dict(start=0.45, end=0.95, text="Договорились записывать время сна."),
        ], "СИНТЕТИЧЕСКАЯ модель — тест"

    def summarize(self, segments, checkpoint):
        checkpoint()
        return (
            dict(
                summary=[
                    dict(
                        text="Обсуждался сон.",
                        quote="Синтетический разговор о сне.",
                        segment_ids=[1],
                    )
                ],
                next_steps=[
                    dict(
                        text="Записывать время сна.",
                        quote="Договорились записывать время сна.",
                        segment_ids=[2],
                    )
                ],
                clarifications=[
                    dict(
                        text="Уточнить детали сна.",
                        quote="разговор о сне.",
                        segment_ids=[1],
                    )
                ],
            ),
            "СИНТЕТИЧЕСКАЯ сводка — тест",
        )


def processed(client, meeting):
    mid, context, _ = meeting
    grants(client, context)
    assert upload(client, mid).status_code == 303
    rid = recording_id(client)
    assert queue(client, rid, "transcript").status_code == 303
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    assert queue(client, rid, "summary").status_code == 303
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    return rid


def summary_form(client, rid, intent="publish"):
    with connect(client.app.state.db_path) as c:
        artifact = service.latest(c, rid, "summary")
        payload = json.loads(artifact["payload"])
    data = {
        "csrf": token(client),
        "revision": str(artifact["id"]),
        "intent": intent,
        "sources_checked": "yes",
        "medicines_checked": "yes",
    }
    for kind, items in payload.items():
        for i, item in enumerate(items):
            data.update(
                {f"{kind}_{i}_text": item["text"], f"{kind}_{i}_included": "yes"}
            )
    return data


def test_no_automatic_consent_or_processing_and_role_scope(client, meeting):
    mid, context, vid = meeting
    assert upload(client, mid).status_code == 403
    with connect(client.app.state.db_path) as c:
        assert not c.execute("SELECT id FROM meeting_recordings").fetchall()
        assert not c.execute(
            "SELECT id FROM consent_events WHERE context_id=?", (context,)
        ).fetchall()
    login(client, "patient2")
    assert client.get(f"/meetings/{mid}").status_code == 403
    assert client.get("/patients/1/meetings").status_code == 403
    login(client, "patient1")
    assert client.get(f"/meetings/{mid}").status_code == 200
    assert upload(client, mid).status_code == 403
    assert (
        client.post(
            f"/patients/1/appointments/{vid}/meeting", data={"csrf": token(client)}
        ).status_code
        == 403
    )


def test_real_wav_upload_dedup_range_and_format(client, meeting):
    mid, context, _ = meeting
    grants(client, context, ["audio_recording"])
    for _ in range(2):
        assert upload(client, mid).status_code == 303
    rid = recording_id(client)
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM meeting_recordings").fetchone()[0] == 1
        r = c.execute("SELECT * FROM meeting_recordings").fetchone()
        assert r["duration"] == 1 and r["mime"] == "audio/wav"
    response = client.get(
        f"/meeting-recordings/{rid}/media", headers={"Range": "bytes=0-43"}
    )
    assert response.status_code == 206 and response.content == audio()[:44]
    assert response.headers["content-range"] == f"bytes 0-43/{len(audio())}"
    assert (
        client.get(
            f"/meeting-recordings/{rid}/media", headers={"Range": "bytes=999999-"}
        ).status_code
        == 416
    )
    assert upload(client, mid, b"not media" * 12).status_code == 422
    assert upload(client, mid, media_kind="video").status_code == 403
    assert queue(client, rid, "transcript").status_code == 403


def test_persistent_queue_sources_review_and_patient_publication(client, meeting):
    rid = processed(client, meeting)
    assert not service.process_one(client.app.state.db_path, SyntheticModels())
    assert queue(client, rid, "transcript").status_code == 303
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM meeting_jobs").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM meeting_artifacts").fetchone()[0] == 2
        assert all(r[0] == "done" for r in c.execute("SELECT status FROM meeting_jobs"))
    login(client, "patient1")
    html = client.get(f"/meetings/{meeting[0]}").text
    assert "Обсуждался сон." not in html and "Уточнить детали сна." not in html
    assert client.get(f"/meeting-recordings/{rid}/export").status_code == 403
    assert queue(client, rid, "summary").status_code == 403
    login(client, "doctor")
    data = summary_form(client, rid)
    assert (
        client.post(
            f"/meeting-recordings/{rid}/summary", data={**data, "medicines_checked": ""}
        ).status_code
        == 422
    )
    assert (
        client.post(f"/meeting-recordings/{rid}/summary", data=data).status_code == 200
    )
    assert (
        client.post(f"/meeting-recordings/{rid}/summary", data=data).status_code == 409
    )
    login(client, "patient1")
    assert "Обсуждался сон." in client.get(f"/meetings/{meeting[0]}").text
    result = client.get(f"/meeting-recordings/{rid}/export").json()
    assert (
        "clarifications" not in result["summary"]
        and result["segments"][0]["start"] == 0
    )
    login(client, "doctor")
    assert (
        client.post(
            f"/meeting-recordings/{rid}/summary",
            data={"csrf": token(client), "intent": "withdraw"},
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert client.get(f"/meeting-recordings/{rid}/export").status_code == 403


def test_revocation_before_and_during_model_never_saves_result(client, meeting):
    mid, context, _ = meeting
    grants(client, context)
    upload(client, mid)
    rid = recording_id(client)
    queue(client, rid, "transcript")

    class Revoking(SyntheticModels):
        def transcribe(self, path, checkpoint):
            result = super().transcribe(path, checkpoint)
            grants(client, context, ["ai_transcription"], "withdrawn")
            # Even if a model ignores its cancellation callback, post-check rejects it.
            return result

    service.process_one(client.app.state.db_path, Revoking())
    with connect(client.app.state.db_path) as c:
        assert not c.execute("SELECT id FROM meeting_artifacts").fetchall()
        assert c.execute("SELECT status FROM meeting_jobs").fetchone()[0] == "cancelled"
    assert queue(client, rid, "transcript").status_code == 403
    grants(client, context)
    queue(client, rid, "transcript")
    grants(client, context, ["ai_transcription"], "withdrawn")

    class MustNotRun(SyntheticModels):
        def transcribe(self, *args):
            pytest.fail("Model must not run after withdrawal")

    service.process_one(client.app.state.db_path, MustNotRun())


def test_retry_sanitized_errors_and_restart_recovery(client, meeting):
    mid, context, _ = meeting
    grants(client, context)
    upload(client, mid)
    rid = recording_id(client)
    queue(client, rid, "transcript")

    class Broken(SyntheticModels):
        def transcribe(self, *args):
            raise RuntimeError("SECRET PATIENT CONTENT")

    service.process_one(client.app.state.db_path, Broken())
    with connect(client.app.state.db_path) as c:
        job = c.execute("SELECT * FROM meeting_jobs").fetchone()
        assert job["status"] == "failed" and job["error_code"] == "processing_failed"
        assert "SECRET" not in str(
            [tuple(r) for r in c.execute("SELECT * FROM meeting_events")]
        )
    queue(client, rid, "transcript")
    with TestClient(create_app(client.app.state.db_path)):
        assert service.process_one(client.app.state.db_path, SyntheticModels())
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT attempts FROM meeting_jobs").fetchone()[0] == 2
        assert len(c.execute("SELECT * FROM meeting_artifacts").fetchall()) == 1
        c.execute(
            "UPDATE meeting_jobs SET status='running',lease_until='2000-01-01',worker_token='gone'"
        )
    service.process_one(client.app.state.db_path, SyntheticModels())
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT error_code FROM meeting_jobs").fetchone()[0]
            == "worker_interrupted"
        )


def test_transcript_correction_versions_and_invalidates_old_summary(client, meeting):
    rid = processed(client, meeting)
    data = summary_form(client, rid)
    client.post(f"/meeting-recordings/{rid}/summary", data=data)
    with connect(client.app.state.db_path) as c:
        transcript = service.latest(c, rid, "transcript")
        segments = json.loads(transcript["payload"])["segments"]
    form = {"csrf": token(client), "revision": str(transcript["id"])}
    for s in segments:
        form.update(
            {
                f'text_{s["id"]}': s["text"],
                f'speaker_{s["id"]}': "Пациент" if s["id"] == 1 else "Врач",
            }
        )
    form["text_1"] = "Исправленный синтетический разговор о сне."
    assert (
        client.post(f"/meeting-recordings/{rid}/transcript", data=form).status_code
        == 200
    )
    assert (
        client.post(f"/meeting-recordings/{rid}/transcript", data=form).status_code
        == 409
    )
    assert (
        client.post(
            f"/meeting-recordings/{rid}/summary", data=summary_form(client, rid)
        ).status_code
        == 409
    )
    with connect(client.app.state.db_path) as c:
        assert service.published(c, rid) is None
        assert (
            json.loads(
                c.execute(
                    "SELECT payload FROM meeting_artifacts WHERE id=?",
                    (transcript["id"],),
                ).fetchone()[0]
            )["segments"][0]["speaker"]
            == "Говорящий не определён"
        )
        assert service.latest(c, rid, "transcript")["author_id"] is not None


def test_explicit_deletion_redacts_all_text_and_cancels_jobs(client, meeting):
    rid = processed(client, meeting)
    endpoint = f"/meeting-recordings/{rid}/delete"
    assert (
        client.post(endpoint, data={"csrf": "bad", "confirmed": "yes"}).status_code
        == 403
    )
    assert client.post(endpoint, data={"csrf": token(client)}).status_code == 422
    assert (
        client.post(
            endpoint, data={"csrf": token(client), "confirmed": "yes"}
        ).status_code
        == 200
    )
    assert client.get(f"/meeting-recordings/{rid}/media").status_code == 410
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT content FROM meeting_recordings").fetchone()[0] is None
        assert all(
            r[0] is None for r in c.execute("SELECT payload FROM meeting_artifacts")
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM meeting_events")
    assert upload(client, meeting[0]).status_code == 303


@pytest.mark.parametrize(
    "overrides", [{"start": float("nan")}, {"end": 3}, {"start": -0.2}, {"text": ""}]
)
def test_bad_transcript_rejected(overrides):
    with pytest.raises(ProcessingError):
        validate_transcript([{**dict(start=0, end=1, text="Тест"), **overrides}], 1)


@pytest.mark.parametrize(
    "item",
    [
        dict(text="Пункт", quote="Придуманная цитата", segment_ids=[1]),
        dict(text="Пункт", quote="Реальная цитата", segment_ids=[99]),
    ],
)
def test_summary_requires_exact_quote_and_real_segment(item):
    with pytest.raises(ProcessingError):
        validate_summary(
            dict(summary=[item], next_steps=[], clarifications=[]),
            [dict(id=1, text="Реальная цитата")],
        )


def test_long_meeting_chunks_cover_every_segment_and_external_provider_forbidden(
    monkeypatch,
):
    source = [dict(id=i, text="Текст " + str(i) + " " * 500) for i in range(50)]
    assert [s["id"] for chunk in chunks(source) for s in chunk] == list(range(50))
    monkeypatch.setenv("COMPASS_OLLAMA_URL", "https://external.test")
    with pytest.raises(ProcessingError):
        configuration()


def test_expiry_purges_new_media_but_keeps_metadata(client, meeting):
    rid = processed(client, meeting)
    with connect(client.app.state.db_path) as c:
        c.execute(
            "UPDATE meeting_recordings SET expires_at='2000-01-01' WHERE id=?", (rid,)
        )
    assert client.get(f"/meeting-recordings/{rid}/media").status_code == 410
    service.process_one(client.app.state.db_path, SyntheticModels())
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "SELECT content FROM meeting_recordings WHERE id=?", (rid,)
            ).fetchone()[0]
            is None
        )
        assert all(
            row[0] is None for row in c.execute("SELECT payload FROM meeting_artifacts")
        )


@pytest.mark.parametrize("cloud", [False, True])
def test_local_ollama_adapter_checks_model_and_citations(monkeypatch, cloud):
    import httpx
    import app.services.meeting_ai as ai

    real_client = httpx.Client
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json=(
                    {"remote_host": "remote.test"}
                    if cloud
                    else {"model_info": {"general.architecture": "qwen2"}}
                ),
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "summary": [
                                {
                                    "text": "Обсуждён сон.",
                                    "quote": "Обсудим качество сна.",
                                    "segment_ids": [1],
                                }
                            ],
                            "next_steps": [],
                            "clarifications": [],
                        }
                    )
                }
            },
        )

    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    if cloud:
        with pytest.raises(ProcessingError):
            ai.LocalModels().summarize(
                [dict(id=1, text="Обсудим качество сна.")], lambda: None
            )
        assert calls == ["/api/show"]
    else:
        result, _ = ai.LocalModels().summarize(
            [dict(id=1, text="Обсудим качество сна.")], lambda: None
        )
        assert result["summary"][0]["segment_ids"] == [1] and calls == [
            "/api/show",
            "/api/chat",
        ]
