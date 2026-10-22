"""Custom questions are owned by the leading doctor; real answers preserve versions."""

from datetime import date
import json
from app.storage import connect
from tests.helpers import login, token
from tests.test_diary_cards import save_entry


def add(client, **changes):
    data = dict(
        csrf=token(client),
        question_builder="yes",
        title="Что помогло сегодня?",
        kind="text",
        category="activity",
        recall_period="Сегодня",
        frequency="daily",
        interval="1",
        trigger="always",
    )
    data.update(changes)
    return client.post("/patients/1/diary/metrics", data=data)


def defs(client):
    from app.services.diary_questions import definitions

    with connect(client.app.state.db_path) as c:
        return definitions(c, 1, True)


def test_custom_options_multiple_time_duration_and_optional_missing_are_durable(client):
    login(client, "doctor")
    for data in [
        dict(kind="multi", options="Прогулка\nМузыка\nОтдых"),
        dict(kind="time", title="Во сколько легли?"),
        dict(kind="duration", title="Сколько гуляли?", unit="мин"),
        dict(kind="choice", title="Как прошёл день?", options="Спокойно\nНапряжённо"),
    ]:
        assert add(client, **data).status_code == 200
    questions = defs(client)
    login(client, "patient1")
    assert client.get("/patients/1/diary/questions/new").status_code == 403
    payload = {}
    for m in questions:
        payload["version_" + str(m["id"])] = str(m["version"])
    payload.update(
        metric_1=["Прогулка", "Музыка"],
        metric_2="23:40",
        metric_3="0",
        status_4="unknown",
    )
    result = save_entry(client, **payload)
    assert result.status_code == 200, result.text
    assert (
        "Прогулка, Музыка" in result.text
        and "23:40" in result.text
        and "Не знаю" in result.text
    )
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM diary_metric_values").fetchone()[0] == 3
        assert c.execute(
            "SELECT status,answer_json FROM diary_answer_details WHERE metric_id=4"
        ).fetchone()[:] == ("unknown", "null")
        assert (
            c.execute(
                "SELECT value FROM diary_metric_values WHERE metric_id=3"
            ).fetchone()[0]
            == "0"
        )
    login(client, "doctor")
    assert "Прогулка, Музыка" in client.get("/patients/1/diary").text


def test_question_edit_pause_and_stale_form_preserve_old_titles_answers(client):
    login(client, "doctor")
    assert add(client, title="Старая подпись", kind="scale").status_code == 200
    login(client, "patient1")
    assert save_entry(client, metric_1="3", version_1="1").status_code == 200
    login(client, "doctor")
    question = defs(client)[0]
    data = dict(
        csrf=token(client),
        question_builder="yes",
        revision="1",
        title="Новая подпись",
        kind="scale",
        category="activity",
        frequency="daily",
        interval="1",
        trigger="always",
    )
    assert (
        client.post("/patients/1/diary/questions/1/save", data=data).status_code == 200
    )
    login(client, "patient1")
    assert save_entry(client, metric_1="8", version_1="1").status_code == 409
    login(client, "doctor")
    assert "Старая подпись:</strong> 3" in client.get("/patients/1/diary").text
    assert (
        client.post(
            "/patients/1/diary/questions/1/active",
            data=dict(csrf=token(client), revision="2", active="no"),
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert "metric-dialog-1" not in client.get("/patients/1/diary").text
    assert save_entry(client, metric_1="7", version_1="3").status_code == 422
    with connect(client.app.state.db_path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 1
        assert (
            c.execute("SELECT count(*) FROM diary_question_versions").fetchone()[0] == 3
        )


def test_required_status_schedule_condition_and_invalid_choices_are_checked_server_side(
    client,
):
    login(client, "doctor")
    assert (
        add(
            client,
            kind="choice",
            options="Один\nДва",
            required="yes",
            trigger="wellbeing_lte",
            threshold="3",
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert save_entry(client, wellbeing="0", version_1="1").status_code == 422
    assert (
        save_entry(client, wellbeing="0", version_1="1", status_1="discuss").status_code
        == 200
    )
    assert (
        save_entry(
            client, wellbeing="0", version_1="1", metric_1="Поддельный вариант"
        ).status_code
        == 422
    )
    assert (
        save_entry(client, wellbeing="9", version_1="1", metric_1="Один").status_code
        == 422
    )
    assert save_entry(client, wellbeing="9").status_code == 200
    assert (
        save_entry(client, wellbeing="9", metric_999="Чужой вопрос").status_code == 422
    )
    login(client, "doctor")
    assert (
        add(
            client,
            frequency="weekdays",
            weekdays=str((date.today().isoweekday() % 7) + 1),
            title="Другой день",
        ).status_code
        == 200
    )
    login(client, "patient1")
    assert (
        save_entry(
            client, wellbeing="9", version_2="1", metric_2="не по расписанию"
        ).status_code
        == 422
    )
