"""A successful upload and its processing result must be visibly reachable."""

from html import unescape
from html.parser import HTMLParser

import pytest

from app.services import meetings as service
from app.services.meeting_ai import ProcessingError
from app.storage import connect
from tests.helpers import login
from tests.test_meetings import (
    meeting,
    grants,
    upload,
    recording_id,
    queue,
    SyntheticModels,
)


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def by_id(self, ident):
        return next(attrs for _, attrs in self.tags if attrs.get("id") == ident)


@pytest.mark.parametrize("calendar", [False, True])
def test_upload_opens_saved_recording_with_visible_confirmation_and_next_action(
    client, meeting, calendar
):
    mid, context, _ = meeting
    grants(client, context)
    response = upload(client, mid, **({"calendar": "true"} if calendar else {}))
    rid = recording_id(client)
    assert response.status_code == 303
    destination = response.headers["location"]
    assert f"recording={rid}&uploaded=new#recording-{rid}" in destination
    assert destination.startswith("/doctor/calendar?") is calendar
    page = client.get(destination)
    assert page.status_code == 200
    assert "open" in Elements(page.text).by_id(f"recording-{rid}")
    assert "Файл загружен и сохранён." in page.text
    assert "Файл загружен · расшифровка не запущена" in page.text
    assert "Чтобы запустить обработку, нажмите" in page.text
    assert "Расшифровать запись" in page.text
    # Visibility is fixed without an implicit model call or processing consent.
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM meeting_jobs").fetchone()[0] == 0
    duplicate = upload(client, mid, **({"calendar": "true"} if calendar else {}))
    assert (
        f"recording={rid}&uploaded=existing#recording-{rid}"
        in duplicate.headers["location"]
    )
    assert "Этот файл уже загружен." in client.get(duplicate.headers["location"]).text
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM meeting_recordings").fetchone()[0] == 1
    default = client.get(f"/meetings/{mid}")
    assert "open" not in Elements(default.text).by_id(f"recording-{rid}")


@pytest.mark.parametrize("calendar", [False, True])
def test_status_is_visible_on_collapsed_row_and_ready_link_opens_actual_text(
    client, meeting, calendar
):
    mid, context, _ = meeting
    grants(client, context)
    upload(client, mid)
    rid = recording_id(client)
    queued = queue(client, rid, "transcript")
    assert queued.status_code == 303
    path = (
        f"/doctor/calendar?patient_id=1&meeting_id={mid}"
        if calendar
        else f"/meetings/{mid}"
    )
    page = client.get(path)
    assert "open" not in Elements(page.text).by_id(f"recording-{rid}")
    assert page.context["records"][0]["list_status"]["state"] == "queued"
    assert "data-recording-status" in page.text and "Расшифровка:" in page.text
    polled = client.get(f"/meetings/{mid}/jobs").json()["jobs"]
    assert polled[0]["recording_id"] == rid and polled[0]["status"] == "queued"
    assert "Синтетический разговор" not in str(polled)
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    page = client.get(path)
    assert page.context["records"][0]["list_status"]["state"] == "done"
    links = [
        attrs["href"]
        for tag, attrs in Elements(page.text).tags
        if tag == "a" and "&recording_view=transcript" in attrs.get("href", "")
    ]
    assert links and all(link.endswith(f"#transcript-{rid}") for link in links)
    ready = client.get(links[0])
    assert ready.status_code == 200
    parsed = Elements(ready.text)
    assert "open" in parsed.by_id(f"recording-{rid}")
    assert "open" in parsed.by_id(f"transcript-{rid}")
    assert "Синтетический разговор о сне." in unescape(ready.text)
    queue(client, rid, "summary")
    assert service.process_one(client.app.state.db_path, SyntheticModels())
    page = client.get(path)
    summary_links = [
        attrs["href"]
        for tag, attrs in Elements(page.text).tags
        if tag == "a" and "&recording_view=summary" in attrs.get("href", "")
    ]
    assert summary_links
    ready_summary = client.get(summary_links[0])
    assert "open" in Elements(ready_summary.text).by_id(f"recording-{rid}")
    assert (
        f'id="summary-{rid}"' in ready_summary.text
        and "Обсуждался сон." in ready_summary.text
    )


def test_failed_processing_is_not_silently_hidden_and_patient_sees_no_private_state(
    client, meeting
):
    mid, context, _ = meeting
    grants(client, context)
    upload(client, mid)
    rid = recording_id(client)
    queue(client, rid, "transcript")

    class Failed(SyntheticModels):
        def transcribe(self, path, checkpoint):
            raise ProcessingError("asr_model_missing")

    assert service.process_one(client.app.state.db_path, Failed())
    page = client.get(f"/meetings/{mid}")
    assert page.context["records"][0]["list_status"]["state"] == "failed"
    assert 'data-recording-state="failed"' in page.text
    assert "open" not in Elements(page.text).by_id(f"recording-{rid}")
    login(client, "patient1")
    page = client.get(f"/meetings/{mid}")
    assert page.context["records"][0]["list_status"] == dict(
        state="idle", label="Итоги ещё не опубликованы"
    )
    assert 'data-recording-state="failed"' not in page.text
    assert (
        "Расшифровать запись" not in page.text and "asr_model_missing" not in page.text
    )
