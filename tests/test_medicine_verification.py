"""Unrestricted catalogue storage and separate, version-bound prescribing permission."""

import json
import secrets
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData
from app.services import (
    drug_library as library,
    doctor_medicines,
    medicine_verification as verification,
    medicine_drafts,
    medicine_reactions,
)
from app.storage import connect
from tests.test_diary_library_offline import store, call
from tests.medicine_verification_helpers import review_form, verify


def add(store, **changes):
    _, path, users = store
    with connect(path) as c:
        return doctor_medicines.save(
            c,
            users["d1"],
            FormData(
                dict(
                    client_token=secrets.token_urlsafe(24),
                    name="Тестовое",
                    source_url="https://example.org/parsed",
                    source_text="Полный полученный текст",
                )
                | changes
            ),
        )


def checked(store, code, **changes):
    _, path, users = store
    with connect(path) as c:
        form = review_form(c, code, **changes)
    return call(store, "/medicines/{code}/verification", "POST", code=code, form=form)


def find(store, code):
    _, path, _ = store
    with connect(path) as c:
        return next(
            d for d in library.search(c, include_archived=True) if d["code"] == code
        )


def scheme(code, revision=0):
    return dict(
        csrf="direct-unit-csrf",
        patient_id="1",
        revision=str(revision),
        intent="save",
        drug=code,
        **{code + ".dose": "Тестовая доза", code + ".frequency": "1 раз в день"},
    )


def save_draft(store, code, revision=0):
    return call(
        store,
        "/patients/{pid}/assignments/medicines",
        "POST",
        pid=1,
        form=scheme(code, revision),
    )


def share(store, revision=1):
    return call(
        store,
        "/patients/{pid}/assignments/medicines/share",
        "POST",
        pid=1,
        form=dict(csrf="direct-unit-csrf", revision=str(revision)),
    )


def test_partial_card_and_resource_remain_viewable_but_prescribing_cannot_be_bypassed(
    store,
):
    code = add(store, name="", inn="", registration="")
    drug = find(store, code)
    assert drug["verification_status"] == "unverified" and not drug["can_prescribe"]
    card = call(store, "/medicines/{slug}", slug=code.lower()).body.decode()
    assert "Не проверен" in card and "https://example.org/parsed" in card
    assert "Полный полученный текст" in card and "data-medicine-verification" in card
    assert "Назначить пациенту</a>" not in card
    catalogue = call(store, "/medicines", q="", group="", status="").body.decode()
    assert f'/medicines/prescriptions?add={code}"' not in catalogue
    with pytest.raises(HTTPException) as error:
        save_draft(store, code)
    assert error.value.status_code == 422
    _, path, users = store
    with connect(path) as c:
        from app.routes.medicine_drafts import draft_context

        with pytest.raises(HTTPException):
            draft_context(c, path, users["d1"], 1, add=[code])
        assert c.execute("SELECT count(*) FROM medicine_drafts").fetchone()[0] == 0
        assert not library.sources(c, code)


@pytest.mark.parametrize(
    "missing",
    [
        "registration_confirmed",
        "instruction_checked",
        *["checked_" + k for k in verification.SECTIONS],
        *["text_" + k for k in verification.SECTIONS],
        *["locator_" + k for k in verification.SECTIONS],
    ],
)
def test_every_required_confirmation_and_instruction_section_is_mandatory(
    store, missing
):
    code = add(store)
    response = checked(store, code, **{missing: ""})
    assert response.status_code == 422
    assert not find(store, code)["can_prescribe"]
    assert "Синтетическое подтверждение" in response.body.decode()


@pytest.mark.parametrize(
    "changes",
    [
        dict(registration_url="https://example.org/not-grls"),
        dict(registration_url="https://grls.rosminzdrav.ru/"),
        dict(instruction_url="javascript:bad"),
        dict(name=""),
        dict(registration="Не уточнён"),
        dict(instruction_version=""),
        dict(valid_until="2000-01-01"),
    ],
)
def test_registration_product_and_source_details_are_required(store, changes):
    code = add(store)
    assert checked(store, code, **changes).status_code == 422
    assert not find(store, code)["can_prescribe"]


def test_complete_review_enables_assignment_and_sharing_with_checked_source_snapshot(
    store,
):
    code = add(store)
    assert checked(store, code).status_code == 303
    drug = find(store, code)
    assert (
        drug["can_prescribe"]
        and drug["prescribing"]["label"] == "Проверен для назначения"
    )
    assert not drug[
        "verified"
    ]  # Separate from legacy, individually verified source fragments.
    card = call(store, "/medicines/{slug}", slug=code.lower()).body.decode()
    assert "Назначить пациенту</a>" in card
    assert save_draft(store, code).status_code == 303
    assert share(store).status_code == 303
    _, path, users = store
    with connect(path) as c:
        item = medicine_drafts.read(c, path, users["d1"], 1)["items"][0]
        assert item["reference"]["status"] == "rf_instruction_verified"
        assert item["reference"]["registration_source"].startswith(
            "https://grls.rosminzdrav.ru/"
        )
        assert (
            item["reference"]["verification_import"]
            == drug["prescribing"]["review"]["_import"]
        )


def test_review_is_idempotent_and_bound_to_one_card_and_current_version(store):
    first, second = add(store), add(store)
    _, path, users = store
    with connect(path) as c:
        form = FormData(review_form(c, first))
        ident = verification.save(c, users["d1"], first, form)
        assert verification.save(c, users["d1"], first, form) == ident
        with pytest.raises(HTTPException) as error:
            verification.save(c, users["d1"], second, form)
        assert error.value.status_code == 409
        drug = library.current(c, "drug")[first]
        drug.pop("_import")
        drug["source_text"] = "Изменённая версия карточки"
        doctor_medicines.write(
            c, users["d1"], first, drug, library.dump(drug).encode(), "update"
        )
        with pytest.raises(HTTPException) as error:
            verification.save(c, users["d1"], first, form)
        assert error.value.status_code == 409
    assert not find(store, first)["can_prescribe"]
    assert "изменились" in find(store, first)["prescribing"]["reason"]


def test_revocation_blocks_unsent_scheme_and_history_remains_readable(store):
    code = add(store)
    checked(store, code)
    assert save_draft(store, code).status_code == 303
    assert checked(store, code, action="withdraw").status_code == 303
    with pytest.raises(HTTPException):
        share(store)
    with pytest.raises(HTTPException):
        save_draft(store, code, revision=1)
    _, path, users = store
    with connect(path) as c:
        assert (
            medicine_drafts.read(c, path, users["d1"], 1, version=1)["items"][0]["code"]
            == code
        )
        assert c.execute("SELECT count(*) FROM medicine_plan_shares").fetchone()[0] == 0
    page = call(store, "/patients/{pid}/assignments", pid=1, version=None).body.decode()
    assert code in page and "непроверенные препараты" in page


def test_new_confirmation_requires_resaving_an_older_scheme_before_new_delivery(store):
    code = add(store)
    checked(store, code)
    save_draft(store, code)
    checked(
        store,
        code,
        note="Повторное синтетическое подтверждение изменило версию проверки.",
    )
    with pytest.raises(HTTPException) as error:
        share(store)
    assert error.value.status_code == 409
    assert save_draft(store, code, revision=1).status_code == 303
    assert share(store, revision=2).status_code == 303


def test_expiry_and_archival_remove_permission_without_erasing_existing_shares(
    store, monkeypatch
):
    code = add(store)
    checked(store, code)
    save_draft(store, code)
    share(store)
    current_day = verification.today()
    monkeypatch.setattr(verification, "today", lambda: current_day + timedelta(days=1))
    assert not find(store, code)["can_prescribe"]
    with pytest.raises(HTTPException):
        save_draft(store, code, 1)
    monkeypatch.setattr(verification, "today", lambda: current_day)
    _, path, users = store
    with connect(path) as c:
        doctor_medicines.remove(c, users["d1"], code)
        assert c.execute("SELECT count(*) FROM medicine_plan_shares").fetchone()[0] == 1
        assert medicine_drafts.read(c, path, users["d1"], 1)["items"]
    assert not find(store, code)["can_prescribe"]


def test_source_fragment_and_imported_registration_claim_do_not_authorize_prescribing(
    store,
):
    _, path, users = store
    with connect(path) as c:
        day = verification.today().isoformat()
        meta = dict(
            drug_code="D01",
            registration="ЛП-тест",
            product_form="Тестовая форма",
            source_url="https://grls.rosminzdrav.ru/synthetic/D01",
            version="тест",
            checked_on=day,
            valid_until=day,
            facts=[
                dict(field="indications", text="Синтетический фрагмент", locator="тест")
            ],
            pairs=[],
        )
        ident = library.preview(
            c,
            "synthetic.pdf",
            b"%PDF-test",
            users["d1"]["id"],
            official=dict(meta=meta, entities=[]),
        )
        library.commit(c, ident)
        c.execute(
            "INSERT INTO drug_source_events(import_id,action,actor_id,note,created_at) VALUES(?,'verify',?,'Тестовая сверка','test')",
            (ident, users["d1"]["id"]),
        )
        drug = next(d for d in library.search(c) if d["code"] == "D01")
        assert drug["verified"] and not drug["can_prescribe"]
        with pytest.raises(HTTPException):
            doctor_medicines.save(
                c,
                users["d1"],
                FormData(
                    dict(
                        client_token=secrets.token_urlsafe(24),
                        verification_status="verified",
                    )
                ),
            )


def test_only_authenticated_doctors_with_csrf_can_confirm_registration(store):
    code = add(store)
    _, path, users = store
    with connect(path) as c:
        form = review_form(c, code)
    for who, csrf in [("p1", "direct-unit-csrf"), ("d1", "wrong")]:
        with pytest.raises(HTTPException) as error:
            call(
                store,
                "/medicines/{code}/verification",
                "POST",
                who=who,
                code=code,
                form=form | dict(csrf=csrf),
            )
        assert error.value.status_code == 403
    assert not find(store, code)["can_prescribe"]


def test_explicit_absence_of_instruction_data_is_preserved_as_checked_text(store):
    code = add(store)
    assert (
        checked(
            store,
            code,
            text_interactions="Нет данных о взаимодействиях согласно проверенной инструкции.",
        ).status_code
        == 303
    )
    assert find(store, code)["prescribing"]["review"]["sections"]["interactions"][
        "text"
    ].startswith("Нет данных")


def test_synthetic_scenarios_import_historical_schemes_without_authorizing_new_prescriptions(
    store,
):
    from scripts.setup_aviandr_scenarios import seed

    _, path, users = store
    with connect(path) as c:
        seed(c, path)
        assert c.execute("SELECT count(*) FROM medicine_plan_shares").fetchone()[0] >= 1
        assert not library.current(c, verification.KIND)
        assert not next(d for d in library.search(c) if d["code"] == "D01")[
            "can_prescribe"
        ]
        assert c.execute("SELECT count(*) FROM aviandr_scenarios").fetchone()[0] == 3


def test_legacy_save_endpoint_cannot_add_an_unverified_medicine(store):
    code = add(store)
    with pytest.raises(HTTPException) as error:
        call(
            store, "/medicines/prescriptions/save", "POST", pid=None, form=scheme(code)
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "changes", [dict(registration="Неизвестно"), dict(name="Лекарство без названия")]
)
def test_unknown_product_identity_cannot_be_confirmed_for_prescribing(store, changes):
    code = add(store)
    assert checked(store, code, **changes).status_code == 422
    assert not find(store, code)["can_prescribe"]
