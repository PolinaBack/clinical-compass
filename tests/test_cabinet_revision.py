from datetime import date
import json
from app.storage import connect, now
from app.services.intake import (
    computations,
    categories_with_progress,
    normalized_answers,
)
from tests.helpers import login, token


def test_one_emergency_entry_patient_only_and_no_reports_or_bottom_nav(client):
    login(client, "patient1")
    for path in [
        "/patients/1/diary",
        "/patients/1/results",
        "/settings",
        "/patients/1/profile/about",
        "/me/materials",
    ]:
        html = client.get(path).text
        assert html.count('href="/help-now"') == 1, path
        assert html.index('aria-label="Навигация пациента"') < html.index("</header>")
        assert "/reports" not in html
    login(client, "doctor")
    for path in [
        "/doctor",
        "/patients/1/results",
        "/settings",
        "/patients/1/profile/family",
        "/doctor/resources",
    ]:
        html = client.get(path).text
        assert 'href="/help-now"' not in html
        assert "/reports" not in html
    assert (
        client.post("/patients/1/reports", data=dict(csrf=token(client))).status_code
        == 410
    )
    assert client.get("/patients/1/reports").status_code == 410


def test_family_brother_sister_custom_unique_parents_and_medical_view(client):
    login(client, "patient1")
    for relation, name in [
        ("mother", "Мама"),
        ("father", "Папа"),
        ("brother", "Иван"),
        ("sister", "Анна"),
    ]:
        assert (
            client.post(
                "/patients/1/relatives",
                data=dict(csrf=token(client), relation=relation, name=name),
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/patients/1/relatives",
            data=dict(csrf=token(client), relation="mother", name="Другая мама"),
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/patients/1/relatives",
            data=dict(csrf=token(client), relation="father", name="Другой папа"),
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/patients/1/relatives",
            data=dict(
                csrf=token(client),
                relation="custom",
                name="Ольга",
                custom_relation="Тётя",
            ),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/patients/1/family/mother",
            data=dict(
                csrf=token(client),
                tab="about",
                name="Елена",
                about="Краткая заметка на листе",
                sex="female",
                age=54,
            ),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/patients/1/family/mother/conditions",
            data=dict(
                csrf=token(client),
                label="Гипертензия",
                kind="known",
                age_kind="Возраст, когда сообщили диагноз",
                age_text="около 48 лет",
                source="Со слов родственников",
            ),
        ).status_code
        == 200
    )
    page = client.get("/patients/1/profile/family").text
    assert all(
        t in page for t in ["Брат", "Сестра", "Тётя", "Гипертензия", "Елена", "54 лет"]
    )
    assert "Описательная семейная история." not in page
    login(client, "doctor")
    html = client.get("/patients/1/profile/family?condition=Гипертензия").text
    assert (
        "Медицинская родословная" in html
        and 'class="affected"' in html
        and "Пробанд" in html
    )
    assert "около 48 лет" in html
    assert (
        client.post(
            "/patients/1/relatives",
            data=dict(csrf=token(client), relation="sister", name="Нет доступа"),
        ).status_code
        == 403
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM relatives WHERE relation='mother'"
            ).fetchone()[0]
            == 1
        )


def test_bmi_units_and_optional_measurement_date_and_old_data_normalization():
    answer = lambda v: dict(status="answered", value=v)
    old = dict(
        personal=dict(
            birth_date=answer("1990-01-01"), height=answer("170"), weight=answer("68")
        )
    )
    modern = normalized_answers(old)
    assert (
        "height" not in modern["personal"]
        and modern["measurements"]["height"]["value"] == "170"
    )
    result = computations(dict(answers=modern), date(2026, 10, 23))
    assert result["bmi"] == 23.5 and "не указана" in result["bmi_note"]
    assert old["personal"]["height"]["value"] == "170"
    modern["measurements"]["height"] = answer("175")
    modern["measurements"]["weight"] = answer("75")
    exact = computations(dict(answers=modern), date(2026, 10, 23))
    assert (
        exact["bmi"] == 24.5
        and "175 см" in exact["bmi_inputs"]
        and "75 кг" in exact["bmi_inputs"]
    )
    modern["measurements"]["height"] = answer("200")
    modern["measurements"]["weight"] = answer("100")
    assert computations(dict(answers=modern), date(2026, 10, 23))["bmi"] == 25.0
    modern["measurements"]["bmi_restriction"] = answer("Беременность")
    assert computations(dict(answers=modern), date(2026, 10, 23))["bmi"] is None


def test_progress_counts_answered_questions_not_files_unknown_or_comments():
    a = lambda v: dict(status="answered", value=v)
    data = dict(
        answers={
            "personal": {
                "preferred_name": a("Имя"),
                "comment": a("Комментарий"),
                "birth_date": {"status": "unknown", "value": None},
            }
        },
        entities=[],
        attachments=[dict(category="personal")],
    )
    card = next(c for c in categories_with_progress(data, {}) if c["key"] == "personal")
    assert card["done"] == 1 and card["total"] > 1 and card["percent"] < 100


def test_sqlite_parent_constraints_preserve_existing_relatives(client):
    import sqlite3
    import pytest

    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/relatives",
            data=dict(csrf=token(client), relation="mother", name="Существующая мать"),
        ).status_code
        == 200
    )
    with connect(client.app.state.db_path) as c:
        with pytest.raises(sqlite3.IntegrityError, match="parent_already_exists"):
            c.execute(
                "INSERT INTO relatives(patient_id,name,relation) VALUES(1,'Дубль','mother')"
            )
        rid = c.execute(
            "INSERT INTO relatives(patient_id,name,relation) VALUES(1,'Брат','brother')"
        ).lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="parent_already_exists"):
            c.execute("UPDATE relatives SET relation='mother' WHERE id=?", (rid,))
        assert c.execute("SELECT count(*) FROM relatives").fetchone()[0] == 2
        assert (
            c.execute("SELECT relation FROM relatives WHERE id=?", (rid,)).fetchone()[0]
            == "brother"
        )
