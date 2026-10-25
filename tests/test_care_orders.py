"""Disposable fictional requests: persistence, disclosure, files, access and history."""

import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import connect
from app.services import care_orders as service, clinical_attachments as files
from scripts.backup import backup
from tests.helpers import login, token
from tests.test_clinical_attachments import PDF
from tests.test_medicine_workspace import scheme, saved


def form(client, pid=1, **changes):
    result = dict(
        csrf=token(client),
        client_token=secrets.token_urlsafe(24),
        kind="lab",
        name="Общий анализ крови",
        due="next_visit",
        purpose="СИНТЕТИЧЕСКАЯ ЦЕЛЬ",
        preparation="Подготовка по указанию специалиста",
        comment="Учебный запрос",
        return_to=f"/patients/{pid}/assignments",
    )
    result.update(changes)
    return result


def send(client, pid=1, **changes):
    return client.post(
        f"/patients/{pid}/assignments/orders",
        data=form(client, pid, **changes),
        follow_redirects=False,
    )


def orders(client):
    with connect(client.app.state.db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM care_orders ORDER BY id")]


def attach(client, oid=1, pid=1, content=PDF, name="synthetic.pdf", **changes):
    return client.post(
        f"/patients/{pid}/assignments/orders/{oid}/files",
        data=dict(csrf=token(client), **changes),
        files={"file": (name, content, "application/octet-stream")},
        follow_redirects=False,
    )


def status(client, intent, oid=1, pid=1, revision=1, **changes):
    return client.post(
        f"/patients/{pid}/assignments/orders/{oid}/status",
        data=dict(
            csrf=token(client),
            intent=intent,
            revision=revision,
            return_to=f"/patients/{pid}/assignments",
            **changes,
        ),
        follow_redirects=False,
    )


def test_navigation_empty_profiles_and_private_drug_draft(client):
    login(client, "doctor")
    page = client.get("/patients/1/workspace/assessments")
    nav = re.search(r'<nav class="tab-nav specialist-nav".*?</nav>', page.text, re.S)[0]
    assert "Анкеты и динамика" in nav and ">Назначения</a>" in nav
    assert (
        "Анкеты, назначения" not in page.text and "Динамика и результаты" in page.text
    )
    assert (
        client.get("/patients/1/workspace/assignments", follow_redirects=False).headers[
            "location"
        ]
        == "/patients/1/assignments"
    )
    assert (
        client.get("/patients/1/profile/assignments", follow_redirects=False).headers[
            "location"
        ]
        == "/patients/1/assignments"
    )
    assert (
        client.post("/medicines/prescriptions/save", data=scheme(client)).status_code
        == 200
    )
    page = client.get("/patients/1/assignments")
    assert page.status_code == 200 and page.context["active_orders"] == []
    assert "/medicines/prescriptions?patient_id=1" in page.text
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert page.status_code == 200 and "Мои назначения и обследования" in page.text
    assert "Отправленных лекарственных назначений пока нет" in page.text
    assert "СИНТЕТИЧЕСКАЯ ДОЗА" not in page.text
    assert saved(client)["revision"] == 1


@pytest.mark.parametrize(
    "kind,name,extra",
    [
        ("lab", "ТТГ", {}),
        (
            "instrumental",
            "УЗИ органов брюшной полости",
            dict(due="date", due_date="2026-10-31"),
        ),
        (
            "consultation",
            "Эндокринолог",
            dict(due="other", due_text="После готовности результатов"),
        ),
        ("advice", "other", dict(custom_name="Свой учебный пункт", due="none")),
    ],
)
def test_send_persists_to_selected_patient_with_all_fields(client, kind, name, extra):
    login(client, "doctor")
    response = send(client, kind=kind, name=name, **extra)
    assert response.status_code == 303
    row = orders(client)[0]
    assert row["kind"] == kind and row["status"] == "assigned"
    assert b"\xd0\xa1\xd0\x98\xd0\x9d" not in row["payload"]
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert "СИНТЕТИЧЕСКАЯ ЦЕЛЬ" in page.text
    assert page.context["active_orders"][0]["name"] == extra.get("custom_name", name)
    assert (
        page.context["active_orders"][0]["preparation"]
        == "Подготовка по указанию специалиста"
    )
    if extra.get("due") == "date":
        assert "До 31.10.2026" in page.text
    login(client, "patient2")
    assert client.get("/patients/2/profile/assignments").context["care_orders"] == []
    assert client.get("/patients/1/profile/assignments").status_code == 403
    with TestClient(create_app(client.app.state.db_path)) as restarted:
        login(restarted, "patient1")
        assert (
            len(
                restarted.get("/patients/1/profile/assignments").context[
                    "active_orders"
                ]
            )
            == 1
        )


def test_double_submit_is_idempotent_and_modified_replay_is_rejected(client):
    login(client, "doctor")
    data = form(client)
    for _ in range(2):
        assert (
            client.post("/patients/1/assignments/orders", data=data).status_code == 200
        )
    assert len(orders(client)) == 1
    data["name"] = "ТТГ"
    assert client.post("/patients/1/assignments/orders", data=data).status_code == 409
    assert client.post("/patients/2/assignments/orders", data=data).status_code == 409
    assert len(orders(client)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        dict(kind="unknown"),
        dict(kind="consultation", name="ТТГ"),
        dict(name="other", custom_name=""),
        dict(due="date", due_date="2026-02-31"),
        dict(due="other", due_text=""),
        dict(client_token="bad"),
        dict(comment="x" * 3001),
    ],
)
def test_invalid_order_cannot_partially_save_and_error_returns_to_workspace(
    client, changes
):
    login(client, "doctor")
    client.get("/patients/1/assignments")
    response = send(client, **changes)
    assert response.status_code == 422
    assert "/patients/1/assignments" in response.text and not orders(client)


def test_csrf_and_patient_cannot_create_or_cancel(client):
    login(client, "doctor")
    assert send(client, csrf="invalid").status_code == 403
    assert send(client).status_code == 303
    login(client, "patient1")
    assert send(client).status_code == 403
    assert status(client, "cancel", confirmed="yes").status_code == 403
    assert status(client, "complete").status_code == 303
    login(client, "patient2")
    assert status(client, "complete").status_code == 403
    assert orders(client)[0]["status"] == "completed"


def test_complete_reopen_cancel_optimistic_revision_and_preserved_history(client):
    login(client, "doctor")
    assert send(client).status_code == 303
    original = orders(client)[0]["payload"]
    login(client, "patient1")
    assert status(client, "complete").status_code == 303
    assert status(client, "reopen").status_code == 409
    assert status(client, "reopen", revision=2).status_code == 303
    login(client, "doctor")
    assert status(client, "cancel", revision=3).status_code == 422
    assert status(client, "cancel", revision=3, confirmed="yes").status_code == 303
    assert orders(client)[0]["payload"] == original
    login(client, "patient1")
    page = client.get("/patients/1/profile/assignments")
    assert page.context["active_orders"] == []
    assert (
        len(page.context["cancelled_orders"]) == 1
        and "Отменено специалистом" in page.text
    )
    assert status(client, "reopen", revision=4).status_code == 409
    assert attach(client).status_code == 409


def test_pdf_upload_real_encryption_doctor_and_patient_access_and_safe_download(
    client, monkeypatch
):
    monkeypatch.setattr(files, "scan", lambda *_: ("not_scanned", ""))
    login(client, "doctor")
    assert send(client).status_code == 303
    login(client, "patient1")
    assert attach(client).status_code == 303
    assert attach(client).status_code == 303
    with connect(client.app.state.db_path) as c:
        row = c.execute("SELECT * FROM care_order_files").fetchone()
        assert c.execute("SELECT count(*) FROM care_order_files").fetchone()[0] == 1
        assert row["content"] != PDF and b"%PDF" not in row["content"]
    url = "/patients/1/assignments/orders/1/files/1"
    assert "Антивирусная проверка не выполнена" in client.get(url).text
    assert client.get(url + "/content").status_code == 409
    assert client.get(url + "/content?download=true&ack_unscanned=true").content == PDF
    login(client, "doctor")
    assert "synthetic.pdf" in client.get("/patients/1/assignments").text
    assert "Прикреплён результат к назначению" in client.get("/doctor").text
    assert client.get(url + "/content?download=true&ack_unscanned=true").content == PDF
    assert (
        client.post(
            url + "/delete", data=dict(csrf=token(client), confirmed="yes")
        ).status_code
        == 403
    )
    login(client, "patient2")
    assert client.get(url).status_code == 403
    assert (
        client.get(url + "/content?download=true&ack_unscanned=true").status_code == 403
    )
    assert client.get(url.replace("patients/1", "patients/2")).status_code == 404
    login(client, "patient1")
    assert (
        client.post(
            url + "/delete", data=dict(csrf=token(client), confirmed="yes")
        ).status_code
        == 200
    )
    assert client.get(url).status_code == 404
    with connect(client.app.state.db_path) as c:
        row = c.execute("SELECT * FROM care_order_files").fetchone()
        assert row["content"] is None and row["deleted_at"]


def test_clean_file_cancel_retains_document_and_invalid_uploads_do_not_save(
    client, monkeypatch
):
    login(client, "doctor")
    send(client)
    monkeypatch.setattr(files, "scan", lambda *_: ("clean", "Synthetic test scanner"))
    for content, name in [
        (b"Not a PDF", "bad.pdf"),
        (PDF + bytes(service.FILE_LIMIT), "big.pdf"),
        (b"%PDF-1.4\n/JavaScript\n%%EOF", "active.pdf"),
    ]:
        assert attach(client, content=content, name=name).status_code == 422
    assert attach(client).status_code == 303
    assert status(client, "cancel", confirmed="yes").status_code == 303
    login(client, "patient1")
    assert client.get("/patients/1/assignments/orders/1/files/1/content").content == PDF
    assert (
        client.get(
            "/patients/1/assignments/orders/1/files/9223372036854775808"
        ).status_code
        == 422
    )


def test_backup_restores_encrypted_requests_and_lost_key_is_not_replaced(
    client, tmp_path
):
    login(client, "doctor")
    send(client)
    target = tmp_path / "protected" / "copy.db"
    backup(client.app.state.db_path, target)
    assert Path(str(target) + ".key").is_file()
    with TestClient(create_app(target)) as restored:
        login(restored, "patient1")
        assert (
            "СИНТЕТИЧЕСКАЯ ЦЕЛЬ" in restored.get("/patients/1/profile/assignments").text
        )
    key = Path(client.app.state.db_path).parent / "attachments.key"
    old = key.with_suffix(".old")
    key.rename(old)
    try:
        assert client.get("/patients/1/assignments").status_code == 503
        assert not key.exists()
    finally:
        old.rename(key)


def test_adverse_groups_are_accessible_native_disclosures_and_filters_keep_all_rows(
    client,
):
    login(client, "doctor")
    page = client.get("/medicines/adverse-effects")
    assert page.text.count('<details class="adverse-system"') == 15
    assert re.search(r'<details class="adverse-system"[^>]*\bopen\b', page.text) is None
    assert "<summary><h2>ЖКТ</h2>" in page.text
    page = client.get("/medicines/adverse-effects?drug=D01&system=ЖКТ")
    assert page.context["count"] == 3
    assert re.search(r'<details class="adverse-system"[^>]*\bopen\b', page.text)
    assert page.text.count("data-adverse-row ") == 468
