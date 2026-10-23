"""Leaf creation is independent of genealogy completeness and preserves history."""

import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login, token


def post(client, path, **data):
    return client.post(path, data=dict(csrf=token(client), **data))


def query(client, sql, args=()):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute(sql, args)]


def test_empty_tree_restores_historical_canopy_and_has_visual_addition(client):
    login(client, "patient1")
    for _ in range(2):
        page = client.get("/patients/1/profile/family")
        assert page.status_code == 200
        assert (
            "family-tree-garden" in page.text
            and "tree-canopy" in page.text
            and "tree-branches" in page.text
        )
        assert "M85 465C-5 330 120 140 280 175" in page.text
        assert (
            'id="family-add-leaf"' in page.text
            and 'id="family-add-dialog"' in page.text
        )
    assert not query(client, "SELECT * FROM relatives")
    assert 'name="name"' in client.get("/patients/1/profile/family/add").text
    assert 'value="sibling"' not in page.text


def test_any_order_no_automatic_ancestors_and_specific_relationships(client):
    login(client, "patient1")
    for relation in [
        "sister",
        "brother",
        "maternal_grandmother",
        "uncle",
        "grandmother",
    ]:
        assert (
            post(client, "/patients/1/relatives", relation=relation).status_code == 200
        )
    relatives = query(client, "SELECT * FROM relatives ORDER BY id")
    assert len(relatives) == 5
    assert [r["relation"] for r in relatives] == [
        "sister",
        "brother",
        "other",
        "uncle",
        "grandmother",
    ]
    assert relatives[2]["slot"] == "maternal_grandmother"
    assert all(r["parent_id"] is None for r in relatives)
    assert not query(
        client, "SELECT * FROM relatives WHERE relation IN ('mother','father')"
    )
    page = client.get("/patients/1/profile/family").text
    assert "Брат" in page and "Сестра" in page and "Дядя" in page
    assert len(query(client, "SELECT * FROM relatives")) == 5
    login(client, "doctor")
    assert "Медицинская родословная" in client.get("/patients/1/profile/family").text
    assert "pedigree-node" in client.get("/patients/1/profile/family").text


def test_custom_category_short_form_edit_and_restart(client):
    login(client, "patient1")
    assert (
        post(
            client,
            "/patients/1/relatives",
            relation="custom",
            custom_relation="Двоюродная сестра",
            name="Ирина",
            age="33",
            sex="female",
            about="Моя заметка",
            deceased="yes",
        ).status_code
        == 200
    )
    r = query(client, "SELECT * FROM relatives")[0]
    key = f"custom-{r['id']}"
    meta = query(client, "SELECT * FROM family_person_meta")[0]
    assert meta["custom_relation"] == "Двоюродная сестра" and meta["age"] == 33
    assert "Ирина" in client.get("/patients/1/profile/family").text
    endpoint = f"/patients/1/family/{key}"
    assert (
        post(
            client,
            endpoint,
            tab="about",
            name="Ирина",
            custom_relation="Крёстная",
            sex="female",
            age="34",
            about="Обновлённая заметка",
            return_to="tree",
        ).url.path
        == "/patients/1/profile/family"
    )
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        page = restarted.get("/patients/1/profile/family").text
        assert "Крёстная" in page and "Обновлённая заметка" in page and "34 лет" in page
        assert len(query(restarted, "SELECT * FROM relatives")) == 1
        assert len(query(restarted, "SELECT * FROM family_details")) == 1
    assert r == query(client, "SELECT * FROM relatives")[0]


def test_duplicate_parents_slots_and_custom_category_validation(client):
    login(client, "patient1")
    for relation in ["mother", "father", "maternal_grandmother"]:
        assert (
            post(client, "/patients/1/relatives", relation=relation).status_code == 200
        )
        assert (
            post(
                client, "/patients/1/relatives", relation=relation, name="Повтор"
            ).status_code
            == 409
        )
    for fields in [
        dict(relation="custom"),
        dict(relation="custom", custom_relation="a" * 81),
        dict(relation="sister", age="131"),
        dict(relation="sister", parent_id="99999"),
        dict(relation="wrong"),
    ]:
        assert post(client, "/patients/1/relatives", **fields).status_code == 422
    assert len(query(client, "SELECT * FROM relatives")) == 3
    page = client.get("/patients/1/profile/family/add").text
    for value in ["mother", "father", "maternal_grandmother"]:
        assert f'value="{value}"' not in page
    assert (
        post(
            client,
            "/patients/1/relatives",
            relation="custom",
            custom_relation="Приёмная мама",
        ).status_code
        == 200
    )


def test_new_dialogs_preserve_existing_health_facts_and_legacy_sources(client):
    login(client, "patient1")
    post(
        client,
        "/patients/1/relatives",
        relation="custom",
        custom_relation="Крёстный",
        name="Андрей",
    )
    key = "custom-1"
    post(
        client,
        f"/patients/1/family/{key}/conditions",
        label="Сведения близкого",
        kind="signs",
        description="Прежнее описание",
        source="Со слов родственников",
    )
    post(
        client,
        f"/patients/1/family/{key}",
        tab="explanations",
        explanation="Уточнить на встрече",
        discuss="true",
    )
    conditions = query(client, "SELECT * FROM family_conditions")
    post(
        client,
        f"/patients/1/family/{key}",
        tab="about",
        name="Андрей",
        about="Короткая заметка",
        sex="male",
        age="50",
        return_to="tree",
    )
    assert conditions == query(client, "SELECT * FROM family_conditions")
    details = query(client, "SELECT * FROM family_details")[0]
    assert details["explanation"] == "Уточнить на встрече" and details["discuss"] == 1
    assert (
        query(client, "SELECT custom_relation FROM family_person_meta")[0][
            "custom_relation"
        ]
        == "Крёстный"
    )
    assert "Сведения близкого" in client.get("/patients/1/profile/family").text
    with connect(client.app.state.db_path) as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_creation_and_modal_save_require_scope_csrf_and_patient_role(client):
    login(client, "patient1")
    assert (
        client.post(
            "/patients/1/relatives", data=dict(csrf="bad", relation="sister")
        ).status_code
        == 403
    )
    assert post(client, "/patients/2/relatives", relation="sister").status_code == 403
    assert client.get("/patients/2/profile/family/add").status_code == 403
    assert not query(client, "SELECT * FROM relatives")
    login(client, "doctor")
    assert post(client, "/patients/1/relatives", relation="sister").status_code == 403
    assert client.get("/patients/1/profile/family/add").status_code == 403
    page = client.get("/patients/1/profile/family").text
    assert "family-add-dialog" not in page and "data-family-add=" not in page


def test_custom_category_is_escaped_in_html_and_embedded_data(client):
    login(client, "patient1")
    category = "</script><script>alert(1)</script>"
    assert (
        post(
            client, "/patients/1/relatives", relation="custom", custom_relation=category
        ).status_code
        == 200
    )
    page = client.get("/patients/1/profile/family").text
    assert category not in page and "&lt;script&gt;" in page
    assert "\\u003c/script\\u003e" in page
