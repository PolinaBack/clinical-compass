"""Service/controller checks without HTTP, an event loop, or a network listener.

These exercise real SQLite, templates and route functions. They do not test HTTP
middleware, browser behavior, or FastAPI's request binding; test_diary_library does.
"""

from datetime import date
import json
import secrets
from urllib.parse import urlencode

from fastapi import HTTPException
import pytest
from starlette.requests import Request
from starlette.datastructures import FormData
from app.main import create_app
from app.storage import connect
from app.services import diary_library as library, diary_questions as questions
from app.services.diary import read_diary_extras


def immediate(coroutine):
    try:
        yielded = coroutine.send(None)
    except StopIteration as result:
        return result.value
    coroutine.close()
    raise AssertionError(f"Unexpected asynchronous suspension: {yielded!r}")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "direct-routes.db"
    app = create_app(path)
    lifespan = app.router.lifespan_context(app)
    immediate(lifespan.__aenter__())
    with connect(path) as c:
        c.execute("INSERT INTO doctors VALUES(2,'Другой тестовый врач')")
        for username, role, pid, did in [
            ("d1", "doctor", None, 1),
            ("p1", "patient", 1, None),
            ("p2", "patient", 2, None),
            ("d2", "doctor", None, 2),
        ]:
            c.execute(
                "INSERT INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,'unused-test-hash',?,?,?)",
                (username, role, pid, did),
            )
        users = {
            row["username"]: dict(row) for row in c.execute("SELECT * FROM accounts")
        }
        for pid in (1, 2):
            c.execute(
                "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(?,'direct-test',1,1,'2026-10-26')",
                (pid,),
            )
    yield app, path, users
    immediate(lifespan.__aexit__(None, None, None))


def call(store, pattern, method="GET", who="d1", form=None, **params):
    app, path, users = store
    user = users[who]
    session = dict(
        account_id=user["id"],
        auth_version=user["auth_version"],
        csrf="direct-unit-csrf",
        user="doctor" if user["role"] == "doctor" else f"patient{user['patient_id']}",
        patient_id=user["patient_id"],
    )
    body = urlencode(form or {}, doseq=True).encode()
    actual_path = pattern
    for key, value in params.items():
        actual_path = actual_path.replace("{" + key + "}", str(value))

    async def receive():
        return dict(type="http.request", body=body, more_body=False)

    scope = dict(
        type="http",
        method=method,
        path=actual_path,
        raw_path=actual_path.encode(),
        query_string=b"",
        headers=[
            (b"host", b"unit.test"),
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(body)).encode()),
        ],
        session=session,
        scheme="http",
        server=("unit.test", 80),
        client=("127.0.0.1", 1000),
        app=app,
    )
    request = Request(scope, receive)
    route = next(
        r
        for r in app.routes
        if getattr(r, "path", None) == pattern and method in r.methods
    )
    result = route.endpoint(request, **params)
    return immediate(result) if hasattr(result, "send") else result


def assign(store, key, pid=1, frequency="daily"):
    return call(
        store,
        "/doctor/diary-cards/{key}/assign",
        "POST",
        form=dict(csrf="direct-unit-csrf", patient_id=str(pid), frequency=frequency),
        key=key,
    )


def submit(store, extra):
    data = dict(
        csrf="direct-unit-csrf",
        day=date.today().isoformat(),
        wellbeing="0",
        sleep="7.5",
        sleep_quality="10",
        adherence="none",
        **extra,
    )
    return call(
        store,
        "/patients/{pid}/diary",
        "POST",
        who="p1",
        form=data,
        pid=1,
        csrf="direct-unit-csrf",
        day=date.today(),
        wellbeing=0,
        sleep=7.5,
        sleep_quality="10",
        adherence="none",
        note="Тест",
        contact=False,
    )


def test_render_all_43_templates_categories_filters_and_both_builder_modes(store):
    page = call(store, "/doctor/diary-cards", patient_id=1, category="", q="")
    assert page.status_code == 200 and len(page.context["cards"]) == 43
    assert (
        page.body.count(b"data-personal-card") == 43
        and len(page.context["categories"]) == 10
    )
    assert sum(c["kind"] == "scale" for c in page.context["cards"]) == 14
    browsing = call(store, "/doctor/diary-cards", patient_id="", category="sleep", q="")
    assert browsing.context["patient"] is None and len(browsing.context["cards"]) == 7
    filtered = call(
        store, "/doctor/diary-cards", patient_id=1, category="sleep", q="время"
    )
    assert {c["template_key"] for c in filtered.context["cards"]} == {
        "ready-bedtime",
        "ready-wake",
        "ready-sleepLatency",
    }
    own = call(store, "/doctor/diary-cards/new", patient_id=1)
    assert b'name="prompt"' in own.body and b'name="palette"' in own.body
    assert b'action="/doctor/diary-cards/new"' in own.body
    old = call(store, "/patients/{pid}/diary/questions/new", pid=1)
    assert b'action="/patients/1/diary/metrics"' in old.body
    home = call(store, "/doctor/library")
    assert "Готовые карточки персонального дневника" in home.body.decode()


def test_all_cards_can_be_assigned_and_patient_answers_reach_doctor_history(store):
    app, path, users = store
    with connect(path) as c:
        for card in library.builtin_cards():
            library.assign(c, users["d1"], 1, card["template_key"], "daily")
        metrics = questions.definitions(c, 1)
    assert len(metrics) == 43
    patient = call(store, "/patients/{pid}/diary", who="p1", pid=1)
    assert patient.body.count(b"data-personal-card") == 43
    assert (
        b"metric-dialog-"
        not in call(store, "/patients/{pid}/diary", who="p2", pid=2).body
    )
    payload = {}
    for metric in metrics:
        key = str(metric["id"])
        kind = metric["kind"]
        payload["version_" + key] = "1"
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
                            else "Тестовый ответ"
                        )
                    )
                )
            )
        )
        if kind == "effects":
            payload.update(
                {
                    "primary_" + key: "Тошнота",
                    "effect_note_" + key: "После еды",
                    "discuss_" + key: "yes",
                }
            )
    assert submit(store, payload).status_code == 303
    with connect(path) as c:
        assert (
            c.execute("SELECT count(*) FROM diary_answer_details").fetchone()[0] == 43
        )
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    doctor = call(store, "/patients/{pid}/diary", pid=1).body.decode()
    assert "Основное: Тошнота" in doctor and "После еды" in doctor


def test_duplicate_assignment_pause_and_reactivation_preserve_versioned_measurements(
    store,
):
    app, path, users = store
    assign(store, "ready-water")
    assign(store, "ready-water")
    with connect(path) as c:
        metrics = questions.definitions(c, 1, True)
    assert len(metrics) == 1
    mid = metrics[0]["id"]
    assert (
        submit(store, {f"version_{mid}": "1", f"metric_{mid}": "1500"}).status_code
        == 303
    )
    call(
        store,
        "/patients/{pid}/diary/questions/{mid}/active",
        "POST",
        form=dict(csrf="direct-unit-csrf", revision="1", active="no"),
        pid=1,
        mid=mid,
    )
    assign(store, "ready-water")
    with connect(path) as c:
        metrics = questions.definitions(c, 1, True)
        assert len(metrics) == 1 and metrics[0]["version"] == 3
        assert read_diary_extras(c, 1)["diary_metric_latest"][mid]["display"] == "1500"


def test_numbers_effect_constraints_and_missing_values_are_atomic(store):
    app, path, users = store
    for key in ("ready-water", "ready-sleepHours", "ready-medEffects", "ready-mood"):
        assign(store, key)
    with connect(path) as c:
        metrics = questions.definitions(c, 1)
    ids = {m["template_key"]: str(m["id"]) for m in metrics}
    w = ids["ready-water"]
    s = ids["ready-sleepHours"]
    e = ids["ready-medEffects"]
    version = {"version_" + str(m["id"]): "1" for m in metrics}
    cases = [
        {"metric_" + w: "1251"},
        {"metric_" + w: "6050"},
        {"metric_" + s: "7.3"},
        {
            "metric_" + e: ["Тошнота", "Рвота", "Диарея", "Запор"],
            "primary_" + e: "Тошнота",
        },
        {"metric_" + e: ["Тошнота"], "primary_" + e: "Запор"},
        {"metric_" + e: ["Тошнота"]},
    ]
    for fields in cases:
        with pytest.raises(HTTPException) as error:
            submit(store, {**version, **fields})
        assert error.value.status_code == 422
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 0
    assert (
        submit(
            store, {**version, "metric_" + w: "1250", "metric_" + s: "7.5"}
        ).status_code
        == 303
    )
    with connect(path) as c:
        assert (
            c.execute(
                "SELECT count(*) FROM diary_metric_values WHERE metric_id=?",
                (int(ids["ready-mood"]),),
            ).fetchone()[0]
            == 0
        )


def test_custom_card_creation_reuse_and_patient_copy_edit_keep_history(store):
    app, path, users = store
    data = dict(
        csrf="direct-unit-csrf",
        client_token=secrets.token_urlsafe(20),
        patient_id="1",
        question_builder="yes",
        title="Своя карточка",
        prompt="Что помогло?",
        kind="multi",
        options="Отдых\nРазговор",
        palette="goals",
        category="activity",
        frequency="daily",
        condition="always",
    )
    call(store, "/doctor/diary-cards/new", "POST", form=data)
    call(store, "/doctor/diary-cards/new", "POST", form=data)
    with connect(path) as c:
        metric = questions.definitions(c, 1)[0]
        assert len(library.cards(c, users["d1"])) == 44
        assert len(library.cards(c, users["d2"])) == 43
        assert metric["palette"] == "goals"
        assert c.execute("SELECT count(*) FROM diary_metrics").fetchone()[0] == 1
    mid = metric["id"]
    submit(store, {f"version_{mid}": "1", f"metric_{mid}": ["Отдых", "Разговор"]})
    call(
        store,
        "/patients/{pid}/diary/questions/{mid}/save",
        "POST",
        form={**data, "title": "Новое название", "revision": "1"},
        pid=1,
        mid=mid,
    )
    with connect(path) as c:
        history = read_diary_extras(c, 1)["diary_metric_latest"][mid]
        assert (
            history["title"] == "Своя карточка"
            and history["display"] == "Отдых, Разговор"
        )
        assert questions.definitions(c, 1)[0]["template_key"] == "custom-1"
        assert library.cards(c, users["d1"])[-1]["title"] == "Своя карточка"


def test_roles_patient_scope_csrf_and_other_doctor_templates_are_checked(store):
    app, path, users = store
    for pattern, params in [
        ("/doctor/diary-cards", dict(patient_id=None, category="", q="")),
        ("/doctor/diary-cards/new", dict(patient_id=None)),
    ]:
        with pytest.raises(HTTPException) as error:
            call(store, pattern, who="p1", **params)
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        call(
            store,
            "/doctor/diary-cards/{key}/assign",
            "POST",
            form=dict(csrf="wrong", patient_id="1"),
            key="ready-water",
        )
    assert error.value.status_code == 403
    with connect(path) as c:
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=2")
        library.save_template(
            c,
            users["d2"],
            FormData(
                dict(
                    question_builder="yes",
                    title="Чужая карточка",
                    kind="text",
                    category="activity",
                    frequency="daily",
                    client_token=secrets.token_urlsafe(20),
                )
            ),
        )
    for key, pid, status in [
        ("ready-water", 2, 403),
        ("custom-1", 1, 404),
        ("unknown", 1, 404),
    ]:
        with pytest.raises(HTTPException) as error:
            assign(store, key, pid)
        assert error.value.status_code == status


def test_frequency_defaults_and_actual_day_schedule(store):
    app, path, users = store
    for key, frequency in [
        ("ready-panic", "event"),
        ("ready-goal", "weekly"),
        ("ready-study", "three_week"),
    ]:
        assign(store, key, frequency=frequency)
    with connect(path) as c:
        items = questions.definitions(c, 1)
    assert items[0]["frequency"] == "event" and not items[0]["required"]
    assert items[1]["interval"] == 7 and questions.due(items[1], library.today())
    assert items[2]["weekdays"] == [1, 3, 5]


def test_old_diary_definitions_still_accept_zero_and_non_numeric_answers(store):
    app, path, users = store
    for data in [
        dict(title="Старый вопрос", kind="scale"),
        dict(title="Логический вопрос", kind="boolean"),
    ]:
        call(
            store,
            "/patients/{pid}/diary/metrics",
            "POST",
            form=dict(csrf="direct-unit-csrf", **data),
            pid=1,
        )
    assert submit(store, dict(metric_1="0", metric_2="false")).status_code == 303
    with connect(path) as c:
        history = read_diary_extras(c, 1)["diary_metric_latest"]
        assert history[1]["display"] == "0" and history[2]["display"] == "Нет"


def test_reaction_keeps_latest_shared_scheme_as_context_without_claiming_actual_intake(
    store,
):
    from app.services.medicine_reactions import pack

    app, path, users = store
    assign(store, "ready-medEffects")
    with connect(path) as c:
        draft = c.execute(
            "INSERT INTO medicine_drafts(patient_id,author_id,revision,payload,created_at,updated_at) VALUES(1,?,2,?,'test','test')",
            (users["d1"]["id"], pack(c, path, {})),
        ).lastrowid
        for revision, name in [
            (1, "Старая отправленная схема"),
            (2, "Новая отправленная схема"),
        ]:
            payload = pack(
                c,
                path,
                dict(
                    items=[
                        dict(
                            code="test-drug",
                            reference=dict(name=name),
                            dose="Тестовая доза",
                        )
                    ]
                ),
            )
            c.execute(
                "INSERT INTO medicine_draft_versions VALUES(?,?,?,'test')",
                (draft, revision, payload),
            )
            c.execute(
                "INSERT INTO medicine_plan_shares(patient_id,draft_id,draft_revision,author_id,payload,created_at) VALUES(1,?,?,?,?,'test')",
                (draft, revision, users["d1"]["id"], payload),
            )
    submit(store, dict(version_1="1", metric_1=["Тошнота"], primary_1="Тошнота"))
    with connect(path) as c:
        answer = json.loads(
            c.execute("SELECT answer_json FROM diary_answer_details").fetchone()[0]
        )
        assert len(answer["medicine_context"]) == 1
        assert answer["medicine_context"][0]["name"] == "Новая отправленная схема"
    page = call(store, "/patients/{pid}/diary", pid=1).body.decode()
    assert "фактический приём требует уточнения" in page
    assert "Связь с препаратом не установлена" in page


def test_custom_library_and_patient_assignment_survive_application_restart(store):
    app, path, users = store
    form = dict(
        question_builder="yes",
        title="Сохраняемая карточка",
        kind="text",
        category="activity",
        frequency="daily",
        client_token=secrets.token_urlsafe(20),
    )
    with connect(path) as c:
        ident = library.save_template(c, users["d1"], FormData(form))
        library.assign(c, users["d1"], 1, f"custom-{ident}", "template")
    restarted = create_app(path)
    lifespan = restarted.router.lifespan_context(restarted)
    immediate(lifespan.__aenter__())
    try:
        page = call(
            (restarted, path, users),
            "/doctor/diary-cards",
            patient_id=1,
            category="own",
            q="",
        )
        assert len(page.context["cards"]) == 1
        assert page.context["cards"][0]["title"] == "Сохраняемая карточка"
        assert page.context["assigned"][f"custom-{ident}"]["active"]
    finally:
        immediate(lifespan.__aexit__(None, None, None))
