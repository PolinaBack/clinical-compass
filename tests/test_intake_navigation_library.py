"""Save-and-continue, optional cycle history and the doctor's existing tools."""

from fastapi.testclient import TestClient
from app.main import create_app
from app.intake_catalog import BY_KEY, VERSION
from app.services import intake
from app.storage import connect
from tests.helpers import login, token


def save(client, category, **values):
    with connect(client.app.state.db_path) as c:
        revision = intake.draft(c, 1)["revision"]
    return client.post(
        f"/patients/1/intake/sections/{category}",
        data=dict(
            csrf=token(client), revision=revision, catalog_version=VERSION, **values
        ),
        follow_redirects=False,
    )


def cycle(client):
    page = client.get("/patients/1/profile/about")
    return next(
        c for c in page.context["intake_categories"] if c["key"] == "reproductive"
    )


def test_sex_is_visible_and_cycle_appears_without_inventing_answers(client):
    login(client, "patient1")
    assert not next(f for f in BY_KEY["personal"]["fields"] if f["key"] == "sex")[
        "extra"
    ]
    assert 'name="a_sex"' in client.get("/patients/1/profile/about/personal").text
    assert cycle(client)["optional"]
    before = client.get("/patients/1/profile/about").context["intake_overall"]["total"]
    assert save(client, "personal", a_sex="Женский").status_code == 303
    assert not cycle(client)["optional"]
    assert (
        client.get("/patients/1/profile/about").context["intake_overall"]["total"]
        == before + 1
    )
    with connect(client.app.state.db_path) as c:
        assert "reproductive" not in intake.draft(c, 1)["answers"]
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert not cycle(restarted)["optional"]
        assert (
            restarted.get("/patients/1/profile/about/personal").context["intake_data"][
                "answers"
            ]["personal"]["sex"]["value"]
            == "Женский"
        )
        assert save(restarted, "personal", s_sex="declined").status_code == 303
        assert cycle(restarted)["optional"]


def test_cycle_history_survives_sex_changes_without_showing_a_hidden_section(client):
    login(client, "patient1")
    save(client, "personal", a_sex="Женский")
    assert (
        save(client, "reproductive", a_menstruation="Да", a_cycle_days="28").status_code
        == 303
    )
    with connect(client.app.state.db_path) as c:
        rev = intake.draft(c, 1)["revision"]
    assert (
        client.post(
            "/patients/1/intake/submit",
            data=dict(csrf=token(client), revision=rev, catalog_version=VERSION),
        ).status_code
        == 200
    )
    save(client, "personal", a_sex="Мужской")
    assert cycle(client)["optional"]
    assert client.get("/patients/1/profile/about/reproductive").status_code == 404
    assert save(client, "reproductive", a_menstruation="Нет").status_code == 422
    with connect(client.app.state.db_path) as c:
        assert (
            intake.draft(c, 1)["answers"]["reproductive"]["cycle_days"]["value"] == "28"
        )
    login(client, "doctor")
    # The clinician sees the sent female snapshot, not the later private draft.
    assert (
        client.get("/patients/1/profile/about/reproductive?submission_id=1").status_code
        == 200
    )
    assert save(client, "personal", a_sex="Мужской").status_code == 403
    login(client, "patient1")
    save(client, "personal", a_sex="Женский")
    assert not cycle(client)["optional"]
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        page = restarted.get("/patients/1/profile/about/reproductive")
        assert (
            page.context["intake_data"]["answers"]["reproductive"]["cycle_days"][
                "value"
            ]
            == "28"
        )


def test_next_saves_before_redirect_and_follows_relevant_sections(client):
    login(client, "patient1")
    result = save(
        client,
        "personal",
        a_sex="Женский",
        a_preferred_name="Сохранено до перехода",
        navigate="next",
    )
    assert result.status_code == 303
    assert result.headers["location"] == "/patients/1/profile/about/measurements"
    assert (
        "Сохранено до перехода" in client.get("/patients/1/profile/about/personal").text
    )
    assert (
        save(client, "daily", navigate="next").headers["location"]
        == "/patients/1/profile/about/reproductive"
    )
    save(client, "personal", a_sex="Мужской")
    assert (
        save(client, "daily", navigate="next").headers["location"]
        == "/patients/1/profile/about/life"
    )
    assert (
        save(client, "goals", navigate="next").headers["location"]
        == "/patients/1/profile/about?view=review"
    )
    # The visual tree is not a forced stop within the questionnaire form sequence.
    assert (
        save(client, "support", navigate="next").headers["location"]
        == "/patients/1/profile/about/occupation"
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert (
            "Сохранено до перехода"
            in restarted.get("/patients/1/profile/about/personal").text
        )


def test_invalid_or_stale_next_does_not_navigate_or_overwrite(client):
    login(client, "patient1")
    with connect(client.app.state.db_path) as c:
        rev = intake.draft(c, 1)["revision"]
    stale = dict(
        csrf=token(client),
        revision=rev,
        catalog_version=VERSION,
        navigate="next",
        a_preferred_name="Старая вкладка",
    )
    save(client, "personal", a_preferred_name="Новые данные")
    r = client.post(
        "/patients/1/intake/sections/personal", data=stale, follow_redirects=False
    )
    assert r.status_code == 409 and "location" not in r.headers
    r = save(client, "personal", a_sex="Несуществующий вариант", navigate="next")
    assert r.status_code == 422 and "location" not in r.headers
    with connect(client.app.state.db_path) as c:
        assert (
            intake.draft(c, 1)["answers"]["personal"]["preferred_name"]["value"]
            == "Новые данные"
        )
    # Autosave remains JSON and never submits the questionnaire to the doctor.
    with connect(client.app.state.db_path) as c:
        rev = intake.draft(c, 1)["revision"]
    r = client.post(
        "/patients/1/intake/sections/personal",
        headers={"Accept": "application/json"},
        data=dict(
            csrf=token(client), revision=rev, catalog_version=VERSION, a_sex="Женский"
        ),
    )
    assert r.status_code == 200 and r.json()["saved"] and "location" not in r.headers
    login(client, "doctor")
    assert "ещё не отправил" in client.get("/patients/1/profile/about").text


def test_library_three_cards_active_navigation_and_forum_at_end_of_tasks(client):
    login(client, "doctor")
    library = client.get("/doctor/library", follow_redirects=False)
    assert library.status_code == 200 and library.headers["cache-control"] == "no-store"
    assert library.text.count('class="library-section"') == 3
    assert "FAQ-форум" not in library.text
    for path, title in [
        ("/medicines", "Лекарства"),
        ("/doctor/resources", "Рекомендованные ресурсы"),
        ("/clinical-tools", "Анкеты"),
    ]:
        assert f'href="{path}"' in library.text and title in library.text
        page = client.get(path)
        assert page.status_code == 200
        assert (
            'href="/doctor/library" aria-label="Библиотека" title="Библиотека"  aria-current="page"'
            in page.text
        )
    assert "FAQ-форум" not in client.get("/doctor/resources").text
    tasks = client.get("/doctor/forums").text.split("</main>")[0]
    assert tasks.index('id="faq-forum"') > tasks.index('id="clinician-forum"')
    assert tasks.rstrip().endswith("</a></section></div>")
    assert 'id="faq-forum"' not in client.get("/doctor/calendar").text
    assert client.get("/doctor/faq").status_code == 200
    login(client, "patient1")
    for path in (
        "/doctor/library",
        "/doctor/resources",
        "/clinical-tools",
        "/medicines",
        "/doctor/faq",
        "/doctor/tasks",
    ):
        assert client.get(path).status_code == 403


def test_optional_cycle_statuses_do_not_assume_menstruation():
    for value in ("Мужской", "Другое", "Предпочитаю обсудить лично"):
        data = dict(
            answers=dict(personal=dict(sex=dict(status="answered", value=value)))
        )
        assert not intake.reproductive_suggested(data)
        data["answers"]["reproductive"] = dict(
            menstruation=dict(status="discuss", value=None)
        )
        assert not intake.reproductive_suggested(data)
    assert not intake.reproductive_suggested(
        dict(answers=dict(personal=dict(sex=dict(status="declined", value="Женский"))))
    )
