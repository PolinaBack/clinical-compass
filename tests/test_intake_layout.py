"""Progress, saved navigation, compact forms and the patient's updated cycle rule."""

from app.intake_catalog import VERSION
from app.storage import connect, now
from app.services import intake
from tests.helpers import login, token
from tests.test_intake import form, save


def test_dashboard_percentage_order_and_removed_legacy_notes_preserve_storage(client):
    login(client, "patient1")
    with connect(client.app.state.db_path) as c:
        c.execute(
            "INSERT INTO patient_profiles(patient_id,biography,updated_at) VALUES(1,?,?)",
            ("Историческая заметка", now()),
        )
    save(client, "personal", a_preferred_name="Синтетическое обращение")
    page = client.get("/patients/1/profile/about")
    html = page.text
    assert html.index("data-intake-intro") < html.index(
        'aria-label="Прогресс первичной анкеты"'
    )
    assert html.index("data-intake-privacy") > html.rindex('class="intake-category"')
    assert "Прежние свободные заметки" not in html and 'name="biography"' not in html
    assert "Историческая заметка" not in html
    assert "data-optional-intake" not in html and "Менструальный" not in html
    overall = page.context["intake_overall"]
    assert overall["percent"] == overall["done"] * 100 // overall["questions"]
    assert 'max="100"' in html and "из 8 вопросов" not in html
    personal = client.get("/patients/1/profile/about/personal").text
    assert "intake-fields-grid" in personal and "intake-answer-options" in personal
    assert (
        "Карточек пока нет" not in personal
    )  # Empty unrelated panels don't lengthen the form.
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "SELECT biography FROM patient_profiles WHERE patient_id=1"
            ).fetchone()[0]
            == "Историческая заметка"
        )
    login(client, "doctor")
    assert "Историческая заметка" not in client.get("/patients/1/profile/about").text


def test_jump_saves_latest_answers_checks_revision_and_rejects_hidden_targets(client):
    login(client, "patient1")
    r = client.post(
        "/patients/1/intake/sections/personal",
        data=form(
            client,
            a_preferred_name="Перед переходом",
            navigate="jump",
            jump_to="mental",
        ),
        follow_redirects=False,
    )
    assert (
        r.status_code == 303
        and r.headers["location"] == "/patients/1/profile/about/mental"
    )
    with connect(client.app.state.db_path) as c:
        saved = intake.draft(c, 1)
        assert (
            saved["answers"]["personal"]["preferred_name"]["value"] == "Перед переходом"
        )
    stale = form(
        client, a_preferred_name="Устаревшее", navigate="jump", jump_to="daily"
    )
    save(client, "personal", a_sex="Мужской")
    assert (
        client.post("/patients/1/intake/sections/personal", data=stale).status_code
        == 409
    )
    for target in ("reproductive", "//example.org", "unknown"):
        assert (
            client.post(
                "/patients/1/intake/sections/personal",
                data=form(
                    client,
                    a_preferred_name="Не сохранять",
                    navigate="jump",
                    jump_to=target,
                ),
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/patients/1/intake/navigate", params=dict(jump_to=target)
            ).status_code
            == 404
        )
    with connect(client.app.state.db_path) as c:
        assert (
            intake.draft(c, 1)["answers"]["personal"]["preferred_name"]["value"]
            == "Перед переходом"
        )
    save(client, "personal", a_sex="Женский")
    r = client.post(
        "/patients/1/intake/sections/personal",
        headers={"Accept": "application/json"},
        data=form(client, a_preferred_name="Последний ответ"),
    )
    assert r.json()["sections"] and any(
        i["key"] == "reproductive" for i in r.json()["sections"]
    )
    r = client.get("/patients/1/intake/navigate?jump_to=family", follow_redirects=False)
    assert (
        r.status_code == 303
        and client.get(r.headers["location"]).url.path == "/patients/1/profile/family"
    )


def test_old_cycle_is_kept_but_hidden_for_nonfemale_and_readable_in_female_snapshot(
    client,
):
    login(client, "patient1")
    save(client, "personal", a_sex="Женский")
    save(client, "reproductive", a_menstruation="Да", a_cycle_days="27")
    assert (
        client.post("/patients/1/intake/submit", data=form(client)).status_code == 200
    )
    save(client, "personal", s_sex="unknown")
    page = client.get("/patients/1/profile/about")
    assert (
        "Менструальный" not in page.text
        and "репродуктивный анамнез"
        not in client.get("/patients/1/profile/about?view=review").text
    )
    assert client.get("/patients/1/profile/about/reproductive").status_code == 404
    assert (
        client.post("/patients/1/intake/submit", data=form(client)).status_code == 200
    )
    login(client, "doctor")
    assert client.get("/patients/1/profile/about/reproductive").status_code == 404
    assert (
        client.get("/patients/1/profile/about/reproductive?submission_id=1").status_code
        == 200
    )
    jump = client.get(
        "/patients/1/intake/navigate?jump_to=reproductive&submission_id=1",
        follow_redirects=False,
    )
    assert jump.headers["location"].endswith("/reproductive?submission_id=1")
    with connect(client.app.state.db_path) as c:
        assert (
            intake.draft(c, 1)["answers"]["reproductive"]["cycle_days"]["value"] == "27"
        )
