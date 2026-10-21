"""Synthetic container bytes for API scope; real capture is checked in Chromium."""

import secrets
from tests.helpers import login, token
from tests.test_navigation_v4 import post
from tests.test_cabinet_v5 import assign_new
from tests.test_workflows import rows


def hcl(client):
    login(client, "doctor")
    assign_new(client, "hcl33")
    aid = rows(client, "SELECT id FROM assessments WHERE scale='hcl33'")[0]["id"]
    login(client, "patient1")
    return aid


def upload(
    client,
    aid,
    nonce=None,
    content=b"\x1a\x45\xdf\xa3" + b"synthetic-local-video" * 5,
    **extra,
):
    return client.post(
        f"/assessments/{aid}/video/recordings",
        data={
            "csrf": token(client),
            "duration": 2,
            "upload_id": nonce or secrets.token_urlsafe(24),
            **extra,
        },
        files={"file": ("synthetic.webm", content, "video/webm")},
    )


def test_camera_is_optional_and_refusal_does_not_create_or_change_answers(client):
    aid = hcl(client)
    html = client.get(f"/assessments/{aid}").text
    assert "Открыть окно записи" in html and "Продолжить без видео" in html
    assert (
        post(client, f"/assessments/{aid}/video/choice", intent="decline").status_code
        == 200
    )
    assert upload(client, aid).status_code == 403
    assert not rows(client, "SELECT * FROM video_recordings")
    assert client.get(f"/assessments/{aid}").status_code == 200
    assert not rows(client, "SELECT * FROM answers")
    assert (
        post(client, f"/assessments/{aid}/video/choice", intent="accept").status_code
        == 422
    )
    assert (
        client.post(
            f"/assessments/{aid}/video/choice",
            data={"csrf": "bad", "intent": "accept", "video_consent": "true"},
        ).status_code
        == 403
    )


def test_video_upload_playback_deletion_and_patient_scope(client):
    aid = hcl(client)
    post(
        client,
        f"/assessments/{aid}/video/choice",
        intent="accept",
        video_consent="true",
    )
    nonce = secrets.token_urlsafe(24)
    response = upload(client, aid, nonce=nonce)
    assert response.status_code == 200
    assert upload(client, aid, nonce=nonce).json() == response.json()
    assert len(rows(client, "SELECT id FROM video_recordings")) == 1
    url = response.json()["url"]
    assert client.get(url).content.startswith(b"\x1a\x45\xdf\xa3")
    assert client.get(url).headers["cache-control"] == "no-store"
    assert not rows(client, "SELECT * FROM answers")
    login(client, "doctor")
    assert client.get(url).status_code == 200
    assert "Сохранённые видеофрагменты" in client.get(f"/assessments/{aid}").text
    assert post(client, url + "/delete").status_code == 403
    login(client, "patient2")
    assert client.get(url).status_code == 403
    assert client.get(f"/assessments/{aid}/video").status_code == 403
    assert post(client, url + "/delete").status_code == 403
    login(client, "patient1")
    assert post(client, url + "/delete").status_code == 200
    assert client.get(url).status_code == 404
    record = rows(client, "SELECT * FROM video_recordings")[0]
    assert record["content"] is None and record["deleted_at"]


def test_revocation_invalid_formats_duration_and_nonces_do_not_store_video(client):
    aid = hcl(client)
    post(
        client,
        f"/assessments/{aid}/video/choice",
        intent="accept",
        video_consent="true",
    )
    assert upload(client, aid, content=b"not-a-video" * 4).status_code == 422
    assert upload(client, aid, duration=601).status_code == 422
    assert upload(client, aid, csrf="wrong").status_code == 403
    post(client, f"/assessments/{aid}/video/choice", intent="decline")
    assert client.get(f"/assessments/{aid}/video/status").json()["status"] == "declined"
    assert upload(client, aid).status_code == 403
    assert not rows(client, "SELECT id FROM video_recordings")
