"""Current-day age and independent uploads after questionnaire panel consolidation."""

from datetime import date
from html.parser import HTMLParser

import pytest

from app.services import intake
from app.storage import connect, now
from tests.helpers import login
from tests.test_intake import save, form, send
from tests.test_assessment_period_resume import older_assessment, step


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            assert not self.form, "nested HTML forms make native submissions unreliable"
            self.form = True

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = False


@pytest.mark.parametrize("legacy_date", ["2100-01-01", "not-a-date"])
def test_age_uses_today_even_with_future_visit_and_legacy_query_or_form(
    client, monkeypatch, legacy_date
):
    login(client, "patient1")
    monkeypatch.setattr(intake, "today", lambda: date(2026, 10, 25))
    assert save(client, "personal", a_birth_date="1990-12-01").status_code == 200
    with connect(client.app.state.db_path) as c:
        user = c.execute(
            "SELECT id FROM accounts WHERE username='patient1'"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO appointments(patient_id,author_id,starts_at,timezone_label,url,fallback,created_at) VALUES(1,?,'2027-02-01T12:00','Europe/Moscow','https://example.test/visit','',?)",
            (user, now()),
        )
    page = client.get(
        "/patients/1/profile/about/personal", params={"as_of": legacy_date}
    )
    assert page.status_code == 200
    assert page.context["intake_calculations"]["age"] == 35
    assert page.context["intake_calculations"]["as_of"] == "2026-10-25"
    assert "Изменить дату расчёта" not in page.text and 'name="as_of"' not in page.text
    reply = client.post(
        "/patients/1/intake/sections/personal",
        data=form(
            client, as_of=legacy_date, a_preferred_name="Синтетическое обращение"
        ),
        headers={"accept": "application/json"},
    )
    assert reply.status_code == 200
    assert reply.json()["calculations"]["age"] == 35
    assert reply.json()["calculations"]["as_of"] == "2026-10-25"
    assert send(client).status_code == 200
    login(client, "doctor")
    review = client.get("/patients/1/profile/about/personal?as_of=2100-01-01")
    assert review.context["intake_calculations"]["age"] == 35


@pytest.mark.parametrize("scale", ["phq4", "phq9", "gad7", "hcl33", "safety6"])
def test_all_questionnaires_keep_independent_audio_form_and_native_answer_saving(
    client, scale
):
    login(client, "patient1")
    aid, _, _ = older_assessment(client, scale)
    page = client.get(f"/assessments/{aid}")
    assert page.status_code == 200
    Structure().feed(page.text)
    assert "data-question-comment" in page.text and 'form="audio-form"' in page.text
    assert "Голосовой комментарий к вопросу" not in page.text
    response = step(client, aid, 1, 0, intent="draft")
    assert response.status_code == 200
    with connect(client.app.state.db_path) as c:
        answer = c.execute(
            "SELECT value FROM answers WHERE assessment_id=? AND item='1'", (aid,)
        ).fetchone()
        assert answer[0] == 0
        assert not c.execute(
            "SELECT 1 FROM audio_notes WHERE assessment_id=?", (aid,)
        ).fetchone()


def test_intake_support_is_single_upload_surface_before_related_cards(client):
    login(client, "patient1")
    for category in ("personal", "mental", "medicines", "exams"):
        page = client.get("/patients/1/profile/about/" + category)
        assert page.status_code == 200
        Structure().feed(page.text)
        assert page.text.count("data-local-recording") == 1
        assert (
            page.text.index("Нужно что-то добавить?")
            < page.text.index("data-local-recording")
            < page.text.index("Связанные сведения")
        )
        assert "Документы и голосовые заметки</h2>" not in page.text
        assert "Если сейчас нечего добавить" not in page.text
        assert "Пропустить раздел</button>" not in page.text
        assert "data-intake-section-state=" not in page.text
    assert (
        "data-intake-bmi" in client.get("/patients/1/profile/about/measurements").text
    )
