"""Ready diary cards are real versioned inputs, scoped to each clinician/patient."""

from collections import Counter
import json
import re
import secrets

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from app.services import diary_library as library, diary_questions as questions
from tests.helpers import login, token, TEST_HASH
from tests.test_diary_cards import save_entry


def actor(c):
    return dict(c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone())


def assign(client, key, pid=1, frequency="daily", **extra):
    return client.post(
        f"/doctor/diary-cards/{key}/assign",
        data=dict(
            csrf=token(client), patient_id=str(pid), frequency=frequency, **extra
        ),
    )


def assigned(client):
    with connect(client.app.state.db_path) as c:
        return questions.definitions(c, 1, True)


def test_complete_catalog_filtering_patient_preview_and_diary_entry_point(client):
    login(client, "doctor")
    assert (
        "Готовые карточки персонального дневника" in client.get("/doctor/library").text
    )
    html = client.get("/patients/1/diary").text
    assert (
        'href="/doctor/diary-cards?patient_id=1">Добавить дополнительный показатель'
        in html
    )
    page = client.get("/doctor/diary-cards?patient_id=1")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    cards = page.context["cards"]
    assert len(cards) == 43 and len({c["template_key"] for c in cards}) == 43
    assert Counter(c["category"] for c in cards) == dict(
        emotions=8,
        sleep=7,
        nutrition=6,
        activity=4,
        medication=6,
        psychotherapy=3,
        somatic=2,
        goals=5,
        specialized=1,
        safety=1,
    )
    assert sum(c["kind"] == "scale" for c in cards) == 14
    assert page.text.count("data-personal-card") == 43
    browsing = client.get("/doctor/diary-cards?patient_id=&category=sleep")
    assert browsing.status_code == 200 and len(browsing.context["cards"]) == 7
    assert (
        "Проектное техническое задание" not in page.text and "v17.pdf" not in page.text
    )
    assert 'href="/doctor/library"' in page.text
    water = next(c for c in cards if c["template_key"] == "ready-water")
    assert (water["unit"], water["minimum"], water["maximum"], water["step"]) == (
        "мл",
        0,
        6000,
        50,
    )
    filtered = client.get("/doctor/diary-cards?patient_id=1&category=sleep&q=время")
    assert {c["template_key"] for c in filtered.context["cards"]} == {
        "ready-bedtime",
        "ready-wake",
        "ready-sleepLatency",
    }
    assert (
        client.get("/doctor/diary-cards?q=несуществующаякарточка").context["cards"]
        == []
    )
    assert not assigned(client)


def test_all_43_cards_assign_and_round_trip_patient_answers_without_cross_patient_leak(
    client,
):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        c.execute("BEGIN IMMEDIATE")
        user = actor(c)
        for card in library.builtin_cards():
            library.assign(c, user, 1, card["template_key"], "daily")
    metrics = assigned(client)
    assert len(metrics) == 43
    login(client, "patient2")
    assert "metric-dialog-" not in client.get("/patients/2/diary").text
    login(client, "patient1")
    html = client.get("/patients/1/diary").text
    assert html.count("data-personal-card") == 43
    assert 'value="5" name="metric_' not in html
    payload = {}
    for metric in metrics:
        key = str(metric["id"])
        payload["version_" + key] = str(metric["version"])
        kind = metric["kind"]
        payload["metric_" + key] = (
            "0"
            if kind in {"number", "scale"}
            else (
                "23:40"
                if kind == "time"
                else (
                    metric["options"][0]
                    if kind == "choice"
                    else (
                        metric["options"][:2]
                        if kind == "multi"
                        else (
                            ["Тошнота", "Головокружение"]
                            if kind == "effects"
                            else "Тестовое наблюдение"
                        )
                    )
                )
            )
        )
        if kind == "effects":
            payload.update(
                {
                    "primary_" + key: "Тошнота",
                    "effect_note_" + key: "После еды, со слов пациента",
                    "discuss_" + key: "yes",
                }
            )
    page = save_entry(client, **payload)
    assert page.status_code == 200, page.text
    assert (
        "Основное: Тошнота" in page.text and "После еды, со слов пациента" in page.text
    )
    with connect(client.app.state.db_path) as c:
        assert (
            c.execute("SELECT count(*) FROM diary_answer_details").fetchone()[0] == 43
        )
        effect = next(m for m in metrics if m["kind"] == "effects")
        row = c.execute(
            "SELECT * FROM diary_answer_details WHERE metric_id=?", (effect["id"],)
        ).fetchone()
        value = json.loads(row["answer_json"])
        assert (
            value["effects"] == ["Тошнота", "Головокружение"]
            and value["primary"] == "Тошнота"
            and row["discuss"] == 1
        )
        assert value["source"] == "Со слов пациента" and value["medicine_context"] == []
    login(client, "doctor")
    assert "Основное: Тошнота" in client.get("/patients/1/diary").text


def test_number_steps_ranges_and_effect_primary_are_validated_atomically(client):
    login(client, "doctor")
    for key in ("ready-water", "ready-sleepHours", "ready-medEffects"):
        assert assign(client, key).status_code == 200
    by_key = {m["template_key"]: m for m in assigned(client)}
    login(client, "patient1")
    base = {"version_" + str(m["id"]): str(m["version"]) for m in by_key.values()}
    water = str(by_key["ready-water"]["id"])
    sleep = str(by_key["ready-sleepHours"]["id"])
    effects = str(by_key["ready-medEffects"]["id"])
    invalid = [
        {"metric_" + water: "6050"},
        {"metric_" + water: "1251"},
        {"metric_" + sleep: "7.3"},
        {"metric_" + sleep: "25"},
        {
            "metric_" + effects: ["Тошнота", "Рвота", "Диарея", "Запор"],
            "primary_" + effects: "Тошнота",
        },
        {"metric_" + effects: ["Тошнота"], "primary_" + effects: "Запор"},
        {"metric_" + effects: ["Тошнота"]},
        {
            "metric_" + effects: ["Выдуманный вариант"],
            "primary_" + effects: "Выдуманный вариант",
        },
    ]
    for change in invalid:
        response = save_entry(client, **base, **change)
        assert response.status_code == 422, response.text
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 0
    assert (
        save_entry(
            client, **base, **{"metric_" + water: "1250", "metric_" + sleep: "7.5"}
        ).status_code
        == 200
    )


def test_unanswered_scale_never_turns_default_slider_position_into_an_answer(client):
    login(client, "doctor")
    assert assign(client, "ready-mood").status_code == 200
    mid = assigned(client)[0]["id"]
    login(client, "patient1")
    assert save_entry(client, **{f"version_{mid}": "1"}).status_code == 200
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM diary_metric_values").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM diary_answer_details").fetchone()[0] == 0


def test_assignment_is_idempotent_pause_reactivate_preserves_old_measurements(client):
    login(client, "doctor")
    for _ in range(2):
        response = assign(client, "ready-water", category="nutrition", q="Вода")
        assert (
            response.status_code == 200 and response.url.path == "/doctor/diary-cards"
        )
    metric = assigned(client)[0]
    mid = metric["id"]
    assert len(assigned(client)) == 1
    login(client, "patient1")
    assert (
        save_entry(
            client, **{f"version_{mid}": "1", f"metric_{mid}": "1500"}
        ).status_code
        == 200
    )
    login(client, "doctor")
    assert (
        client.post(
            f"/patients/1/diary/questions/{mid}/active",
            data=dict(csrf=token(client), revision="1", active="no"),
        ).status_code
        == 200
    )
    assert assign(client, "ready-water").status_code == 200
    assert len(assigned(client)) == 1 and assigned(client)[0]["version"] == 3
    assert "Вода:</strong> 1500" in client.get("/patients/1/diary").text


def test_custom_library_card_survives_restart_and_patient_edits_keep_previous_answers(
    client,
):
    login(client, "doctor")
    page = client.get("/doctor/diary-cards/new?patient_id=1")
    assert (
        page.status_code == 200
        and 'name="prompt"' in page.text
        and 'name="palette"' in page.text
    )
    nonce = re.search(r'name="client_token" value="([^"]+)"', page.text)[1]
    data = dict(
        csrf=token(client),
        client_token=nonce,
        patient_id="1",
        question_builder="yes",
        title="Мой показатель",
        prompt="Что было полезным?",
        kind="multi",
        options="Отдых\nРазговор",
        category="activity",
        palette="goals",
        frequency="daily",
        condition="always",
    )
    assert client.post("/doctor/diary-cards/new", data=data).status_code == 200
    assert client.post("/doctor/diary-cards/new", data=data).status_code == 200
    metric = assigned(client)[0]
    mid = metric["id"]
    assert len(assigned(client)) == 1 and metric["palette"] == "goals"
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "doctor")
        own = restarted.get("/doctor/diary-cards?category=own")
        assert (
            len(own.context["cards"]) == 1
            and own.context["cards"][0]["prompt"] == "Что было полезным?"
        )
    login(client, "patient1")
    assert (
        save_entry(
            client, **{f"version_{mid}": "1", f"metric_{mid}": ["Отдых", "Разговор"]}
        ).status_code
        == 200
    )
    login(client, "doctor")
    change = {**data, "title": "Новый заголовок", "revision": "1"}
    assert (
        client.post(f"/patients/1/diary/questions/{mid}/save", data=change).status_code
        == 200
    )
    assert (
        "Мой показатель:</strong> Отдых, Разговор"
        in client.get("/patients/1/diary").text
    )
    assert assigned(client)[0]["template_key"].startswith("custom-")
    assert (
        client.get("/doctor/diary-cards?category=own").context["cards"][0]["title"]
        == "Мой показатель"
    )


def test_library_role_patient_and_custom_ownership_checks(client):
    login(client, "patient1")
    assert client.get("/doctor/diary-cards").status_code == 403
    assert client.get("/doctor/diary-cards/new").status_code == 403
    assert assign(client, "ready-water").status_code == 403
    login(client, "doctor")
    csrf = token(client)
    assert (
        client.post(
            "/doctor/diary-cards/ready-water/assign",
            data=dict(csrf="wrong", patient_id="1"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/doctor/diary-cards/ready-water/assign", data=dict(csrf=csrf)
        ).status_code
        == 422
    )
    assert assign(client, "unknown-card").status_code == 404
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой специалист')")
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
        other = c.execute(
            "INSERT INTO accounts(username,password_hash,role,doctor_id) VALUES('another',?,'doctor',2)",
            (TEST_HASH,),
        ).lastrowid
        c.execute(
            "INSERT INTO diary_card_templates(owner_id,definition,client_token,created_at) VALUES(?,?,?,'2026-10-26')",
            (
                other,
                json.dumps(dict(title="Чужой закрытый шаблон")),
                secrets.token_urlsafe(20),
            ),
        )
    assert client.get("/doctor/diary-cards?patient_id=2").status_code == 403
    assert assign(client, "ready-water", pid=2).status_code == 403
    assert assign(client, "custom-1").status_code == 404
    assert "Чужой закрытый шаблон" not in client.get("/doctor/diary-cards").text


def test_frequencies_do_not_require_daily_answers_for_event_cards(client):
    login(client, "doctor")
    assert assign(client, "ready-panic", frequency="event").status_code == 200
    assert assign(client, "ready-pleasure", frequency="weekly").status_code == 200
    assert assign(client, "ready-study", frequency="three_week").status_code == 200
    definitions = assigned(client)
    assert definitions[0]["frequency"] == "event" and not definitions[0]["required"]
    assert definitions[1]["frequency"] == "interval" and definitions[1]["interval"] == 7
    assert definitions[2]["frequency"] == "weekdays" and definitions[2]["weekdays"] == [
        1,
        3,
        5,
    ]
    assert questions.due(definitions[1], library.today())
