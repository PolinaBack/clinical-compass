"""Family facts remain patient-reported through editing and upgrades."""

from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from tests.helpers import login
from tests.test_navigation_v4 import post
from tests.test_workflows import rows


def add_fact(client, key="mother", **extra):
    return post(
        client,
        f"/patients/1/family/{key}/conditions",
        label="Вымышленное описание",
        kind="signs",
        **extra,
    )


def test_fact_can_be_added_to_empty_leaf_without_creating_other_relatives(client):
    login(client, "patient1")
    assert not rows(client, "SELECT * FROM relatives")
    response = add_fact(client)
    assert response.status_code == 200
    relative = rows(client, "SELECT * FROM relatives")[0]
    assert len(rows(client, "SELECT * FROM relatives")) == 1
    assert relative["slot"] == "mother"
    fact = rows(client, "SELECT * FROM family_conditions")[0]
    assert fact["kind"] == "signs" and fact["source"] == "Не знаю"
    assert fact["age_kind"] == "Не знаю" and fact["age_text"] == ""
    assert "Описание признаков со слов пользователя" in response.text
    assert "Ответы о 1 из 6 родственников" in response.text
    assert 'id="family"' in client.get("/patients/1/profile/family").text
    assert (
        "Вымышленное описание" in client.get("/patients/1/profile/family/summary").text
    )
    login(client, "patient2")
    assert client.get("/patients/1/profile/family/summary").status_code == 403


def test_inline_fact_update_preserves_description_and_requires_patient_scope(client):
    login(client, "patient1")
    add_fact(client, description="Учебный рассказ близкого")
    original = rows(client, "SELECT * FROM family_conditions")[0]
    cid = original["id"]
    endpoint = f"/patients/1/family/conditions/{cid}/update"
    data = dict(
        age_kind="Возраст, когда сообщили диагноз",
        age_text="Около 48 лет",
        source="Со слов родственников",
        uncertain="true",
    )
    assert client.post(endpoint, data={"csrf": "bad", **data}).status_code == 403
    assert (
        post(client, endpoint, **(data | {"source": "Неизвестный вариант"})).status_code
        == 422
    )
    assert rows(client, "SELECT * FROM family_conditions")[0] == original
    assert post(client, endpoint, **data).status_code == 200
    updated = rows(client, "SELECT * FROM family_conditions")[0]
    assert updated == original | {
        "age_kind": data["age_kind"],
        "age_text": data["age_text"],
        "source": data["source"],
        "uncertain": 1,
    }
    login(client, "doctor")
    assert post(client, endpoint, **data).status_code == 403
    page = client.get("/patients/1/profile/family/member/mother?tab=knowledge")
    assert (
        "Учебный рассказ близкого" in page.text
        and "Сохранить карточку" not in page.text
    )
    login(client, "patient2")
    assert post(client, endpoint, **data).status_code == 403
    assert (
        post(client, f"/patients/2/family/conditions/{cid}/update", **data).status_code
        == 404
    )
    login(client, "patient1")
    assert post(client, endpoint, **(data | {"uncertain": "false"})).status_code == 200
    assert rows(client, "SELECT uncertain FROM family_conditions")[0]["uncertain"] == 0


def test_add_condition_validates_before_creating_leaf_and_escapes_text(client):
    login(client, "patient1")
    assert add_fact(client, source="wrong").status_code == 422
    assert not rows(client, "SELECT * FROM relatives")
    assert add_fact(client, key="unknown-key").status_code == 404
    response = post(
        client,
        "/patients/1/family/mother/conditions",
        label="<script>synthetic</script>",
        kind="known",
    )
    assert response.status_code == 200
    assert "<script>synthetic</script>" not in response.text
    assert "&lt;script&gt;synthetic&lt;/script&gt;" in response.text


def test_upgrade_keeps_legacy_facts_and_does_not_assume_they_are_diagnoses(tmp_path):
    path = tmp_path / "legacy-family.db"
    with TestClient(create_app(path)) as client:
        login(client, "patient1")
        add_fact(client)
    with connect(path) as c:
        c.execute("ALTER TABLE family_conditions DROP COLUMN kind")
        c.execute("UPDATE family_conditions SET source='Со слов родственника'")
    for _ in range(2):
        with TestClient(create_app(path)) as client:
            login(client, "patient1")
            page = client.get("/patients/1/profile/family/member/mother?tab=knowledge")
            assert page.status_code == 200
            assert "Сведения со слов пользователя" in page.text
            assert "Со слов родственников" in page.text
            fact = rows(client, "SELECT * FROM family_conditions")[0]
            assert fact["kind"] == "unspecified"
            assert fact["source"] == "Со слов родственника"
            assert fact["label"] == "Вымышленное описание"
