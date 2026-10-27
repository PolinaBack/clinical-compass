"""Real document handlers, form parsing, encryption, SQLite and templates; no HTTP listener."""

from copy import deepcopy
from datetime import date
import json
import secrets

from fastapi import HTTPException
import pytest
from starlette.datastructures import FormData
from app.services import (
    patient_extracts as service,
    intake,
    clinical_attachments as files,
)
from app.services.extract_sources import collect
from app.services.clinical_forms import today
from app.storage import connect
from tests.test_diary_library_offline import store, call


def create(store):
    response = call(
        store,
        "/patients/{pid}/profile/extracts/new",
        "POST",
        pid=1,
        form=dict(csrf="direct-unit-csrf", client_token=secrets.token_urlsafe(20)),
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[-2])


def current(store, eid):
    _, path, users = store
    with connect(path) as c:
        return service.read(c, path, users["d1"], 1, eid, edit=True)


def form_for(doc, **changes):
    data = doc["data"]
    form = dict(csrf="direct-unit-csrf", revision=str(doc["revision"]), intent="save")
    for key in (
        "title",
        "organization",
        "specialist",
        "recipient",
        "diagnosis_code",
        "diagnosis",
        "diagnosis_status",
        "conclusion",
        "recommendations",
        "additional",
    ):
        form[key] = data[key]
    for key, value in data["identity"].items():
        form["identity_" + key] = value
    for section in data["source"]["sections"]:
        form["source_" + section["key"]] = section["text"]
        if section["included"]:
            form["include_" + section["key"]] = "yes"
    if data["reviewed"]:
        form["reviewed"] = "yes"
    form.update(changes)
    return form


def save(store, eid, form):
    return call(
        store,
        "/patients/{pid}/profile/extracts/{eid}/save",
        "POST",
        pid=1,
        eid=eid,
        form=form,
    )


def seed(store):
    _, path, users = store
    answer = lambda value: dict(status="answered", value=value)
    data = dict(
        answers=dict(
            personal=dict(
                full_name=answer("Исходное ФИО"),
                birth_date=answer("2000-01-01"),
                sex=answer("Женский"),
            ),
            measurements=dict(height=answer("165"), weight=answer("68")),
            complaints=dict(comment=answer("Жалобы из отправленной анкеты")),
            life=dict(
                disclosure=answer("Да, могу написать"),
                comment=answer("Значимое событие из анамнеза"),
            ),
        ),
        entities=[],
        attachments=[],
        preferences={},
    )
    with connect(path) as c:
        c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(1,1,'unit',?,?,?)",
            (
                json.dumps(data, ensure_ascii=False),
                users["p1"]["id"],
                today().isoformat(),
            ),
        )
        c.execute(
            "INSERT INTO intake_drafts(patient_id,revision,catalog_version,answers_json,updated_at) VALUES(1,2,'unit',?,'test')",
            (
                json.dumps(
                    dict(
                        personal=dict(full_name=answer("PRIVATE_DRAFT_MARKER")),
                        complaints=dict(comment=answer("PRIVATE_DRAFT_MARKER")),
                    )
                ),
            ),
        )
        c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(1,'2026-10-01','2026-10-14','test')"
        )
        sid = c.execute("SELECT max(id) FROM assessment_sessions").fetchone()[0]
        for scale, status in [("phq4", "completed"), ("gad7", "draft")]:
            aid = c.execute(
                "INSERT INTO assessments(session_id,patient_id,scale,version,status,completed_at) VALUES(?,1,?,'unit',?,?)",
                (sid, scale, status, "2026-10-14" if status == "completed" else None),
            ).lastrowid
            for i in range(1, 5 if scale == "phq4" else 3):
                c.execute(
                    "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,?,0,'answered','test')",
                    (aid, str(i)),
                )
    return data


def test_main_extracts_tab_and_empty_patient_shelf_without_doctor_drafts(store):
    patient = call(
        store,
        "/patients/{pid}/profile/{section}",
        who="p1",
        pid=1,
        section="about",
        view="tree",
    )
    assert 'href="/patients/1/profile/extracts"' in patient.body.decode()
    assert "profile-extracts-entry" not in patient.body.decode()
    assert "Выписки" in call(store, "/patients/{pid}/profile", pid=1).body.decode()
    eid = create(store)
    page = call(store, "/patients/{pid}/profile/extracts", who="p1", pid=1)
    assert not page.context["documents"]
    with pytest.raises(HTTPException) as error:
        call(store, "/patients/{pid}/profile/extracts/{eid}", who="p1", pid=1, eid=eid)
    assert error.value.status_code == 404


def test_sources_include_submitted_intake_scores_and_all_sections_without_private_draft(
    store,
):
    seed(store)
    eid = create(store)
    page = call(store, "/patients/{pid}/profile/extracts/{eid}/edit", pid=1, eid=eid)
    html = page.body.decode()
    assert "PRIVATE_DRAFT_MARKER" not in html
    assert (
        "Жалобы из отправленной анкеты" in html
        and "Значимое событие из анамнеза" in html
    )
    assert "Подтверждённый диагноз" in html and "Без нозологического диагноза" in html
    doc = current(store, eid)
    assert doc["data"]["identity"]["full_name"] == "Исходное ФИО"
    assert (
        len([s for s in doc["data"]["source"]["sections"] if s["group"] == "Анамнез"])
        == 16
    )
    tests = [
        s for s in doc["data"]["source"]["sections"] if s["group"] == "Анкеты и тесты"
    ]
    assert len(tests) == 2
    assert any("Получен результат 0 баллов" in s["text"] for s in tests)
    assert any(
        "Анкета не завершена" in s["text"] and "Получен результат" not in s["text"]
        for s in tests
    )
    assert doc["data"]["diagnosis"] == "" and doc["data"]["diagnosis_code"] == ""


def test_preview_has_no_write_and_print_confirmation_is_available_on_same_form(store):
    seed(store)
    eid = create(store)
    doc = current(store, eid)
    response = save(
        store, eid, form_for(doc, intent="preview", conclusion="ПРОБНЫЙ ТЕКСТ")
    )
    assert response.status_code == 200 and "ПРОБНЫЙ ТЕКСТ" in response.body.decode()
    assert current(store, eid)["revision"] == 1
    bad = save(store, eid, form_for(doc, intent="print"))
    assert (
        bad.status_code == 422
        and 'name="reviewed"' in bad.body.decode()
        and "Подтвердить заполненные сведения" in bad.body.decode()
    )
    assert current(store, eid)["revision"] == 1
    assert (
        save(store, eid, form_for(doc, intent="print", reviewed="yes")).status_code
        == 303
    )
    assert current(store, eid)["published_revision"] is None
    print_page = call(
        store,
        "/patients/{pid}/profile/extracts/{eid}/print",
        pid=1,
        eid=eid,
        revision=2,
    )
    assert (
        print_page.status_code == 200
        and "data-print-document" in print_page.body.decode()
    )
    assert "ОБРАЗЕЦ · НЕ ДЕЙСТВИТЕЛЕН" in print_page.body.decode()


def test_clinician_corrections_publish_snapshot_and_never_rewrite_intake(store):
    seed(store)
    eid = create(store)
    data = form_for(
        current(store, eid),
        intent="publish",
        reviewed="yes",
        identity_full_name="Исправленное ФИО",
        organization="Организация",
        specialist="Врач Тестовый",
        diagnosis_code="F42.1",
        diagnosis="Формулировка врача",
        diagnosis_status="preliminary",
        conclusion="Заключение специалиста",
    )
    assert save(store, eid, data).status_code == 303
    page = call(
        store, "/patients/{pid}/profile/extracts/{eid}", who="p1", pid=1, eid=eid
    )
    text = page.body.decode()
    assert (
        "Исправленное ФИО" in text
        and "Предварительный диагноз" in text
        and "Формулировка врача" in text
    )
    assert "data-extract-editor" not in text and "PRIVATE_DRAFT_MARKER" not in text
    _, path, users = store
    with connect(path) as c:
        assert (
            intake.read_submission(c, 1)["data"]["answers"]["personal"]["full_name"][
                "value"
            ]
            == "Исходное ФИО"
        )
        payload = c.execute(
            "SELECT payload FROM patient_extract_versions ORDER BY revision DESC"
        ).fetchone()[0]
        assert "Исправленное ФИО".encode() not in payload
        assert (
            service.read(c, path, users["p1"], 1, eid)["data"]["source"].get(
                "calculation_data"
            )
            is None
        )
    doc = current(store, eid)
    assert (
        save(
            store, eid, form_for(doc, conclusion="PRIVATE_UNPUBLISHED_EDIT")
        ).status_code
        == 303
    )
    patient = call(
        store, "/patients/{pid}/profile/extracts/{eid}", who="p1", pid=1, eid=eid
    ).body.decode()
    assert (
        "Заключение специалиста" in patient
        and "PRIVATE_UNPUBLISHED_EDIT" not in patient
    )
    with pytest.raises(HTTPException) as error:
        call(
            store,
            "/patients/{pid}/profile/extracts/{eid}/print",
            who="p1",
            pid=1,
            eid=eid,
            revision=3,
        )
    assert error.value.status_code == 404


def test_patient_other_patient_and_other_clinician_cannot_edit_or_create(store):
    eid = create(store)
    for who, pattern, params, method in [
        (
            "p1",
            "/patients/{pid}/profile/extracts/{eid}/edit",
            dict(pid=1, eid=eid),
            "GET",
        ),
        ("p1", "/patients/{pid}/profile/extracts/new", dict(pid=1), "POST"),
        ("p2", "/patients/{pid}/profile/extracts/{eid}", dict(pid=1, eid=eid), "GET"),
        (
            "d2",
            "/patients/{pid}/profile/extracts/{eid}/edit",
            dict(pid=1, eid=eid),
            "GET",
        ),
    ]:
        with pytest.raises(HTTPException) as error:
            call(
                store,
                pattern,
                method,
                who=who,
                form=dict(
                    csrf="direct-unit-csrf", client_token=secrets.token_urlsafe(20)
                ),
                **params
            )
        assert error.value.status_code in (403, 404)
    with pytest.raises(HTTPException) as error:
        save(store, eid, form_for(current(store, eid), csrf="wrong"))
    assert error.value.status_code == 403


def test_stale_form_returns_editable_text_without_overwriting_other_window(store):
    eid = create(store)
    old = current(store, eid)
    assert save(store, eid, form_for(old, conclusion="Первое окно")).status_code == 303
    stale = save(
        store, eid, form_for(old, conclusion="Второе окно — сохранить для копирования")
    )
    assert (
        stale.status_code == 409
        and "Второе окно — сохранить для копирования" in stale.body.decode()
    )
    assert "Открыть актуальную версию" in stale.body.decode()
    assert current(store, eid)["data"]["conclusion"] == "Первое окно"


def test_refresh_reloads_shared_data_and_keeps_manual_document_changes(store):
    seed(store)
    eid = create(store)
    doc = current(store, eid)
    key = "intake-complaints"
    form = form_for(
        doc,
        **{
            "source_" + key: "Ручное уточнение для документа",
            "identity_full_name": "Исправленное имя",
        }
    )
    assert save(store, eid, form).status_code == 303
    _, path, users = store
    with connect(path) as c:
        original = intake.read_submission(c, 1)["data"]
        changed = deepcopy(original)
        changed["answers"]["life"]["comment"]["value"] = "Новый отправленный ответ"
        c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(1,3,'unit',?,?,?)",
            (
                json.dumps(changed, ensure_ascii=False),
                users["p1"]["id"],
                today().isoformat(),
            ),
        )
    assert (
        save(
            store, eid, form_for(current(store, eid), intent="refresh", reviewed="yes")
        ).status_code
        == 303
    )
    doc = current(store, eid)
    sections = {s["key"]: s for s in doc["data"]["source"]["sections"]}
    assert sections[key]["text"] == "Ручное уточнение для документа"
    assert "Новый отправленный ответ" in sections["intake-life"]["text"]
    assert (
        doc["data"]["identity"]["full_name"] == "Исправленное имя"
        and not doc["data"]["reviewed"]
    )
    assert save(store, eid, form_for(doc)).status_code == 303


def test_withdrawal_hides_published_document_and_preserves_immutable_versions(store):
    seed(store)
    eid = create(store)
    assert (
        save(
            store, eid, form_for(current(store, eid), intent="publish", reviewed="yes")
        ).status_code
        == 303
    )
    assert (
        save(store, eid, form_for(current(store, eid), intent="withdraw")).status_code
        == 303
    )
    with pytest.raises(HTTPException) as error:
        call(store, "/patients/{pid}/profile/extracts/{eid}", who="p1", pid=1, eid=eid)
    assert error.value.status_code == 404
    _, path, _ = store
    import sqlite3

    with connect(path) as c:
        assert (
            c.execute("SELECT count(*) FROM patient_extract_versions").fetchone()[0]
            == 2
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE patient_extract_versions SET payload=?", (b"changed",))


def test_missing_key_is_reported_without_replacing_key_or_changing_saved_payload(store):
    eid = create(store)
    _, path, users = store
    key = files.key_path(path)
    assert key.parent == path.parent
    original = key.read_bytes()
    key.unlink()
    try:
        with connect(path) as c:
            with pytest.raises(HTTPException) as error:
                service.read(c, path, users["d1"], 1, eid, edit=True)
            assert error.value.status_code == 503
        assert not key.exists()
    finally:
        key.write_bytes(original)


def test_invalid_identity_stays_in_editor_and_unselected_text_is_not_published(store):
    seed(store)
    eid = create(store)
    doc = current(store, eid)
    response = save(store, eid, form_for(doc, identity_birth_date="2099-01-01"))
    assert response.status_code == 422 and "2099-01-01" in response.body.decode()
    data = form_for(
        doc,
        reviewed="yes",
        intent="publish",
        **{"source_intake-life": "PRIVATE_UNSELECTED_TEXT"}
    )
    data.pop("include_intake-life")
    assert save(store, eid, data).status_code == 303
    page = call(
        store, "/patients/{pid}/profile/extracts/{eid}", who="p1", pid=1, eid=eid
    )
    assert "PRIVATE_UNSELECTED_TEXT" not in page.body.decode()
    _, path, users = store
    with connect(path) as c:
        public = service.read(c, path, users["p1"], 1, eid)["data"]
        assert "PRIVATE_UNSELECTED_TEXT" not in json.dumps(public, ensure_ascii=False)
