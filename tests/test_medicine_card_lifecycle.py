"""Partial imported cards and catalogue removal preserve patient history."""

from collections import Counter
from fastapi.testclient import TestClient
from app.services import drug_library as lib, medicine_web
from app.storage import connect
from tests.helpers import login, token
from tests.test_doctor_medicines import form
from tests.test_medicine_workspace import scheme, saved
from tests.test_medicine_reactions import share
from tests.test_clinician_forum import second_doctor


def create(client, **changes):
    response = client.post("/medicines/new/save", data=form(client, **changes))
    assert response.status_code == 200, response.text
    return response.url.path.rsplit("/", 1)[-1].upper()


def test_single_word_partial_parse_saves_with_resource_and_missing_inn(
    client, monkeypatch
):
    login(client, "doctor")
    monkeypatch.setattr(
        medicine_web,
        "fetch",
        lambda url: (
            "<h1>Пример</h1><h2>Побочные действия</h2><p>Синтетический текст раздела.</p>",
            url,
        ),
    )
    page = client.post(
        "/medicines/new/fetch",
        data=dict(csrf=token(client), source_url="https://example.org/partial"),
    )
    assert page.status_code == 200
    values = page.context["values"]
    result = client.post(
        "/medicines/new/save",
        data=values
        | dict(csrf=token(client), client_token=page.context["client_token"]),
    )
    assert result.status_code == 200
    assert "<h1>Пример</h1>" in result.text and "<h2>Ресурс</h2>" in result.text
    assert 'href="https://example.org/partial"' in result.text
    assert "Синтетический текст раздела." in result.text
    assert "Удалить лекарство из библиотеки" in result.text
    with connect(client.app.state.db_path) as c:
        record = lib.current(c, "drug")[result.url.path.rsplit("/", 1)[-1].upper()]
        assert (
            record["inn"] == ""
            and record["source_url"] == "https://example.org/partial"
        )
    empty = create(client, name="", inn="", source_url="https://example.org/unknown")
    assert (
        "<h1>Лекарство без названия</h1>"
        in client.get("/medicines/" + empty.lower()).text
    )


def test_remove_hides_catalogue_entry_without_erasing_prescriptions_or_history(client):
    login(client, "doctor")
    code = create(client, name="Учебный", inn="", source_url="https://example.org/item")
    assert (
        client.post(
            "/patients/1/assignments/medicines", data=scheme(client, codes=(code,))
        ).status_code
        == 200
    )
    assert share(client).status_code == 303
    original_reference = saved(client)["items"][0]["reference"]
    with connect(client.app.state.db_path) as c:
        before = {
            t: Counter(tuple(r) for r in c.execute("select * from " + t))
            for t in [
                "patients",
                "accounts",
                "medicine_drafts",
                "medicine_draft_versions",
                "medicine_plan_shares",
                "drug_entities",
            ]
        }
    peer = TestClient(client.app)
    login(peer, "doctor")
    peer.post(
        "/medicines/combinations/selection",
        data=dict(csrf=token(peer), action="add", code=code, return_to="/medicines"),
    )
    endpoint = "/medicines/" + code.lower() + "/delete"
    removed = client.post(endpoint, data=dict(csrf=token(client)))
    assert (
        removed.status_code == 200
        and "Лекарство удалено из библиотеки." in removed.text
    )
    assert client.post(endpoint, data=dict(csrf=token(client))).status_code == 200
    assert client.get("/api/medicines/suggest?q=Учебный").json()["results"] == []
    with connect(client.app.state.db_path) as c:
        assert code not in {d["code"] for d in lib.search(c)}
        assert lib.current(c, "drug")[code]["archived"] is True
        for table, rows in before.items():
            assert not rows - Counter(
                tuple(r) for r in c.execute("select * from " + table)
            ), table
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert not c.execute("pragma foreign_key_check").fetchall()
    archived = client.get("/medicines/" + code.lower())
    assert (
        "Удалено из библиотеки." in archived.text
        and "https://example.org/item" in archived.text
    )
    assert "Назначить пациенту</a>" not in archived.text
    assert peer.get("/medicines/combinations").status_code == 200
    assert peer.get("/medicines/combinations?drug=D01&drug=" + code).status_code == 200
    peer.close()
    view = client.get("/patients/1/assignments")
    assert 'data-draft-item="' + code + '"' in view.text
    assert 'data-pick-draft="' + code + '"' not in view.text
    assert client.get("/patients/2/assignments?add=" + code).status_code == 422
    assert (
        client.post(
            "/patients/1/assignments/medicines",
            data=scheme(client, revision=1, codes=(code, "D02")),
        ).status_code
        == 422
    )
    assert saved(client)["items"][0]["reference"] == original_reference
    login(client, "patient1")
    view = client.get("/patients/1/profile/assignments")
    assert "Учебный" in view.text and "СИНТЕТИЧЕСКАЯ ДОЗА" in view.text


def test_delete_requires_author_role_and_csrf_and_preserves_builtin_cards(client):
    login(client, "doctor")
    code = create(client, name="Авторская")
    endpoint = "/medicines/" + code.lower() + "/delete"
    assert client.post(endpoint, data=dict(csrf="wrong")).status_code == 403
    assert (
        client.post("/medicines/d01/delete", data=dict(csrf=token(client))).status_code
        == 403
    )
    second_doctor(client)
    assert client.post(endpoint, data=dict(csrf=token(client))).status_code == 403
    login(client, "patient1")
    assert client.post(endpoint, data=dict(csrf=token(client))).status_code == 403
    with connect(client.app.state.db_path) as c:
        assert code in {d["code"] for d in lib.search(c)}
        assert not lib.current(c, "drug")[code].get("archived")
