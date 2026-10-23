"""Real SQLite lifecycle, linked self-reports, role limits and immutable submissions."""

from datetime import date
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.intake_catalog import CATEGORIES, VERSION
from app.services import intake
from app.storage import connect
from tests.helpers import login, token


def revision(client):
    with connect(client.app.state.db_path) as c:
        return intake.draft(c, 1)["revision"]


def form(client, **changes):
    return dict(
        csrf=token(client),
        revision=str(revision(client)),
        catalog_version=VERSION,
        **changes,
    )


def save(client, category, **fields):
    return client.post(
        f"/patients/1/intake/sections/{category}", data=form(client, **fields)
    )


def send(client):
    return client.post("/patients/1/intake/submit", data=form(client))


def entity(client, kind, **fields):
    return client.post(
        f"/patients/1/intake/entities/{kind}/save", data=form(client, **fields)
    )


def test_catalogue_pages_and_private_draft_publication_versions(client):
    login(client, "patient1")
    html = client.get("/patients/1/profile/about").text
    assert "Первичная анкета" in html and 'name="biography"' not in html
    for category in CATEGORIES:
        r = client.get("/patients/1/profile/about/" + category["key"])
        assert r.status_code == (404 if category["key"] == "reproductive" else 200), r.text
    assert (
        save(
            client,
            "personal",
            a_preferred_name="Тестовое обращение",
            a_birth_date="1995-06-01",
        ).status_code
        == 200
    )
    login(client, "doctor")
    assert "Тестовое обращение" not in client.get("/patients/1/profile/about").text
    assert "ещё не отправил" in client.get("/patients/1/profile/about").text
    login(client, "patient1")
    assert send(client).status_code == 200
    assert (
        save(client, "personal", a_preferred_name="Личный новый черновик").status_code
        == 200
    )
    login(client, "doctor")
    page = client.get("/patients/1/profile/about").text
    assert "Тестовое обращение" in page and "Личный новый черновик" not in page
    assert client.get("/patients/1/profile/about/personal").status_code == 200
    assert save(client, "personal", a_preferred_name="Подмена").status_code == 403
    login(client, "patient1")
    assert send(client).status_code == 200
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        assert (
            "Личный новый черновик" in restarted.get("/patients/1/profile/about").text
        )
        assert (
            "Тестовое обращение"
            in restarted.get("/patients/1/profile/about?submission_id=1").text
        )


def test_unknown_no_zero_branching_and_stale_form_do_not_overwrite(client):
    login(client, "patient1")
    assert (
        save(
            client,
            "mental",
            a_mood="0",
            s_anxiety="unknown",
            s_attention="not_remembered",
            s_energy="discuss",
            a_hospitalization="Нет",
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        values = intake.draft(c, 1)["answers"]["mental"]
        assert values["mood"] == dict(status="answered", value=0)
        assert values["anxiety"] == dict(status="unknown", value=None)
        assert values["attention"]["status"] == "not_remembered"
        assert values["energy"]["status"] == "discuss"
        assert values["hospitalization"]["value"] == "Нет"
    stale = form(client, a_mood="8")
    assert save(client, "mental", a_mood="5").status_code == 200
    assert (
        client.post("/patients/1/intake/sections/mental", data=stale).status_code == 409
    )
    assert (
        save(
            client,
            "occupation",
            a_status="Временно не работаю",
            a_work_role="Поддельное скрытое поле",
        ).status_code
        == 422
    )
    assert save(client, "personal", a_birth_date="2099-01-01").status_code == 422
    assert save(client, "personal", a_weight="NaN").status_code == 422
    assert send(client).status_code == 200
    login(client, "doctor")
    page = client.get("/patients/1/profile/about/mental").text
    assert "Не помню" in page and "Обсудить лично" in page and "Не знаю" in page


def test_conditions_medication_visit_links_and_clinician_review_remain_self_reports(
    client,
):
    login(client, "patient1")
    assert (
        entity(
            client,
            "condition",
            a_system="Эндокринная",
            a_name="Щитовидка, название не помню",
            s_onset="not_remembered",
        ).status_code
        == 200
    )
    assert (
        entity(
            client,
            "medication",
            a_name="Сообщённый препарат",
            a_prescribed_scheme="По словам пациента одна схема",
            a_actual_use="Принимаю иначе",
            a_actual_scheme="Другой самоотчёт",
            related="1",
        ).status_code
        == 200
    )
    assert (
        entity(
            client,
            "visit",
            a_specialty="Эндокринолог",
            a_period="Более года назад",
            related="1",
        ).status_code
        == 200
    )
    login(client, "doctor")
    assert client.get("/patients/1/intake/entities/1").status_code == 404
    login(client, "patient1")
    assert send(client).status_code == 200
    login(client, "doctor")
    for path in [
        "/patients/1/intake/entities/1",
        "/patients/1/intake/entities/2",
        "/patients/1/intake/entities/3",
        "/patients/1/profile/about?view=medicines",
    ]:
        assert client.get(path).status_code == 200
    response = client.post(
        "/patients/1/intake/review",
        data=dict(
            csrf=token(client),
            submission_id="1",
            target="entity:1:1",
            status="clarify",
            note="Уточнить исходное название",
        ),
    )
    assert response.status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM intake_entity_links").fetchone()[0] == 2
        raw = json.loads(
            c.execute("SELECT data_json FROM intake_entities WHERE id=1").fetchone()[0]
        )
        assert raw["name"]["value"] == "Щитовидка, название не помню"
        assert c.execute("SELECT status FROM intake_reviews").fetchone()[0] == "clarify"
    login(client, "patient2")
    assert client.get("/patients/1/intake/entities/1").status_code == 403
    assert (
        client.post(
            "/patients/1/intake/review",
            data=dict(
                csrf=token(client),
                submission_id=1,
                target="entity:1:1",
                status="confirmed",
            ),
        ).status_code
        == 403
    )


def test_bmi_is_computation_not_diagnosis_and_context_can_disable_it():
    answers = {
        "personal": {
            k: dict(status="answered", value=v)
            for k, v in dict(
                birth_date="1990-01-01",
                height="170",
                weight="68",
                measured_at="2026-10-01",
            ).items()
        }
    }
    assert intake.computations(dict(answers=answers), date(2026, 10, 22))["bmi"] == 23.5
    assert (
        intake.computations(dict(answers=answers, preferences={"hide_weight": True}))[
            "bmi"
        ]
        is None
    )
    answers["personal"]["birth_date"]["value"] = "2015-01-01"
    assert intake.computations(dict(answers=answers))["bmi"] is None
    answers["personal"]["birth_date"]["value"] = "1990-01-01"
    answers["reproductive"] = {
        "pregnancy": dict(status="answered", value="Беременность")
    }
    assert intake.computations(dict(answers=answers))["bmi"] is None


def test_inactive_answers_stay_private_and_submitted_question_texts_are_frozen(
    client, monkeypatch
):
    import copy
    from app import intake_catalog

    login(client, "patient1")
    assert (
        save(
            client,
            "occupation",
            a_status="Работаю",
            a_work_role="Личный скрытый черновик",
        ).status_code
        == 200
    )
    assert save(client, "occupation", a_status="Учусь").status_code == 200
    assert save(client, "mental", a_mood="5").status_code == 200
    assert send(client).status_code == 200
    with connect(client.app.state.db_path) as c:
        snapshot = json.loads(
            c.execute("SELECT payload FROM intake_submissions").fetchone()[0]
        )
        assert "work_role" not in snapshot["answers"]["occupation"]
        assert (
            intake.draft(c, 1)["answers"]["occupation"]["work_role"]["value"]
            == "Личный скрытый черновик"
        )
        assert snapshot["catalog"]["categories"]
    # A future catalogue edit cannot relabel the old submitted measurement.
    changed = copy.deepcopy(intake_catalog.CATEGORIES)
    next(
        f
        for c in changed
        if c["key"] == "mental"
        for f in c["fields"]
        if f["key"] == "mood"
    )["label"] = "Другая формулировка будущей версии"
    monkeypatch.setattr(intake, "CATEGORIES", changed)
    login(client, "doctor")
    page = client.get("/patients/1/profile/about/mental?submission_id=1").text
    assert (
        "Настроение за последние две недели" in page
        and "Другая формулировка" not in page
    )
    assert (
        "Личный скрытый черновик"
        not in client.get("/patients/1/profile/about/occupation?submission_id=1").text
    )


def test_family_link_only_accepts_own_tree_and_patient_name_lookup_never_prescribes(
    client,
):
    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/family/mother",
            data=dict(
                csrf=token(client),
                tab="about",
                name="Учебная мама",
                about="Синтетические сведения",
            ),
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        own = c.execute(
            "SELECT id FROM relatives WHERE patient_id=1 AND slot='mother'"
        ).fetchone()[0]
    assert (
        entity(
            client,
            "family",
            a_relative_id=str(own),
            a_relationship="Мать",
            a_condition="Сообщение о здоровье",
        ).status_code
        == 200
    )
    assert (
        entity(
            client, "family", a_relative_id="999999", a_relationship="Отец"
        ).status_code
        == 422
    )
    response = client.get("/patients/1/intake/medication-names?q=Ави")
    assert response.status_code == 200 and any(
        "Авиандр" in name for name in response.json()["names"]
    )
    assert set(response.json()) == {"names"} and all(
        isinstance(name, str) for name in response.json()["names"]
    )
    assert client.get("/api/medicines/suggest?q=Ави").status_code == 403
    assert client.get("/patients/2/intake/medication-names?q=Ави").status_code == 403
