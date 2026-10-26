"""Catalogue additions from manual input or labelled synthetic public-page fixtures."""

from collections import Counter
import json
import socket
import secrets

import httpx
import pytest
from fastapi import HTTPException

from app.services import drug_library as lib, medicine_web
from app.storage import connect
from tests.helpers import login, token

HTML = """<html><head><script type="application/ld+json">{"@type":"Drug","name":"Тестовая карточка","nonProprietaryName":"Синтетическое вещество","dosageForm":"Учебная форма","manufacturer":{"name":"Тестовый производитель"},"availableStrength":{"strengthValue":"20","strengthUnit":"мг"}}</script></head><body><h1>Тестовая карточка</h1><div>Код АТХ: N00XX00</div><h2>Показания</h2><p>ТЕХНИЧЕСКАЯ ФИКСТУРА: не медицинское показание.</p><h2>Противопоказания</h2><p>ТЕХНИЧЕСКАЯ ФИКСТУРА ограничений.</p><h2>Побочное действие</h2><p>ТЕХНИЧЕСКАЯ ФИКСТУРА реакций.</p><script>window.bad=1</script></body></html>"""


def transport(handler, addresses=("8.8.8.8",)):
    def resolver(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return medicine_web.PublicTransport(
        resolver=resolver, inner=httpx.MockTransport(handler)
    )


def form(client, **changes):
    page = client.get("/medicines/new")
    return (
        dict(
            csrf=token(client),
            client_token=page.context["client_token"],
            name="Синтетический новый препарат",
            inn="Техническое вещество",
            group="Учебная группа",
            form="Учебная форма",
            strength="Тестовое значение",
            manufacturer="Вымышленный производитель",
            section_indications="Фикстура для теста, не медицинские сведения.",
        )
        | changes
    )


def test_manual_card_is_a_real_shared_catalogue_entry_and_keeps_old_rows(client):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        before = {
            table: Counter(tuple(r) for r in c.execute("select * from " + table))
            for table in (
                "patients",
                "accounts",
                "assessments",
                "drug_entities",
                "drug_imports",
                "drug_source_events",
            )
        }
    data = form(client)
    response = client.post("/medicines/new/save", data=data)
    assert response.status_code == 200, response.text
    code = response.url.path.rsplit("/", 1)[-1].upper()
    assert code.startswith("U") and "medicine-toc" in response.text
    assert (
        data["manufacturer"] in response.text
        and data["section_indications"] in response.text
    )
    assert client.post("/medicines/new/save", data=data).url.path == response.url.path
    assert (
        client.post(
            "/medicines/new/save", data=data | {"name": "Другие сведения"}
        ).status_code
        == 409
    )
    assert (
        client.get("/api/medicines/suggest?q=Техническое").json()["results"][0]["code"]
        == code
    )
    assert "Найдено: 1." in client.get("/medicines?q=Техническое").text
    with connect(client.app.state.db_path) as c:
        for table, rows in before.items():
            assert not rows - Counter(
                tuple(r) for r in c.execute("select * from " + table)
            ), table
        assert len(lib.current(c, "drug")) == 13
        assert not next(d for d in lib.search(c) if d["code"] == code)["verified"]
        assert not lib.sources(c)
        assert c.execute("select count(*) from aviandr_rules").fetchone()[0] == 0
    assert (
        client.get(f"/medicines/combinations?drug=D01&drug={code}").status_code == 200
    )
    page = client.get("/patients/1/assignments?add=" + code)
    assert page.status_code == 200 and data["name"] in page.text


def test_same_name_and_missing_metadata_do_not_block_another_card(client):
    login(client, "doctor")
    data = form(client, form="", strength="", manufacturer="")
    assert client.post("/medicines/new/save", data=data).status_code == 200
    duplicate = data | {"client_token": secrets.token_urlsafe(24)}
    result = client.post("/medicines/new/save", data=duplicate)
    assert result.status_code == 200
    with connect(client.app.state.db_path) as c:
        assert len(lib.current(c, "drug")) == 14


def test_link_fetch_reads_real_adapter_output_before_explicit_save(client, monkeypatch):
    login(client, "doctor")
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML
        )

    original = medicine_web.fetch
    monkeypatch.setattr(
        medicine_web, "fetch", lambda url: original(url, transport=transport(serve))
    )
    response = client.post(
        "/medicines/new/fetch",
        data=dict(csrf=token(client), source_url="https://example.org/drug"),
    )
    assert response.status_code == 200
    values = response.context["values"]
    assert values["inn"] == "Синтетическое вещество" and values["strength"] == "20 мг"
    assert (
        values["manufacturer"] == "Тестовый производитель"
        and values["form"] == "Учебная форма"
    )
    assert "ТЕХНИЧЕСКАЯ ФИКСТУРА" in values["section_indications"]
    assert calls[0].url.host == "8.8.8.8"
    assert (
        calls[0].headers["host"] == "example.org"
        and calls[0].extensions["sni_hostname"] == "example.org"
    )
    with connect(client.app.state.db_path) as c:
        assert len(lib.current(c, "drug")) == 12
    result = client.post(
        "/medicines/new/save",
        data={
            k: v
            for k, v in values.items()
            if k
            in {
                "source_url",
                *response.context["metadata"],
                *("section_" + s for s in response.context["sections"]),
            }
        }
        | dict(csrf=token(client), client_token=response.context["client_token"]),
    )
    assert result.status_code == 200 and "Тестовая карточка" in result.text
    assert "https://example.org/drug" in result.text
    assert "<script>window.bad=1</script>" not in result.text


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "224.0.0.1"],
)
def test_fetch_never_connects_to_private_or_multicast_addresses(address):
    calls = []
    with pytest.raises(HTTPException):
        medicine_web.fetch(
            "https://example.org/drug",
            transport=transport(lambda request: calls.append(request), [address]),
        )
    assert not calls


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/drug",
        "file:///etc/passwd",
        "https://user:password@example.org/",
        "https://example.org:444/",
        "https://localhost/drug",
    ],
)
def test_unsupported_links_are_rejected(url):
    with pytest.raises(HTTPException):
        medicine_web.checked_url(url)


def test_redirect_rechecks_destination_and_body_is_bounded():
    calls = []

    def resolver(host, port, **kwargs):
        address = "127.0.0.1" if host == "internal.invalid" else "8.8.8.8"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    def serve(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "https://internal.invalid/private"}
        )

    pinned = medicine_web.PublicTransport(
        resolver=resolver, inner=httpx.MockTransport(serve)
    )
    with pytest.raises(HTTPException):
        medicine_web.fetch("https://example.org/drug", transport=pinned)
    assert len(calls) == 1
    with pytest.raises(HTTPException, match="2 МБ"):
        medicine_web.fetch(
            "https://example.org/drug",
            transport=transport(
                lambda r: httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    content=b"x" * (medicine_web.MAX_HTML + 1),
                )
            ),
        )


def test_fetch_error_leaves_manual_form_usable_and_no_card_is_created(
    client, monkeypatch
):
    login(client, "doctor")

    def fail(url):
        raise HTTPException(422, "Сайт недоступен, заполните вручную")

    monkeypatch.setattr(medicine_web, "fetch", fail)
    result = client.post(
        "/medicines/new/fetch",
        data=dict(csrf=token(client), source_url="https://example.org/drug"),
    )
    assert result.status_code == 422 and "data-new-medicine" in result.text
    assert "Сайт недоступен" in result.text
    with connect(client.app.state.db_path) as c:
        assert len(lib.current(c, "drug")) == 12


def test_library_structure_and_removed_pages_have_no_dead_ui_links(client):
    login(client, "doctor")
    home = client.get("/doctor/library")
    assert "/medicines/adverse-effects" not in home.text
    assert "forum-entry-grid library-entry-grid" in home.text
    for path in (
        "/medicines",
        "/medicines/adverse-effects",
        "/medicines/combinations",
        "/clinical-tools",
        "/doctor/resources",
        "/medicines/new",
    ):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "library-back" in page.text and "← Библиотека" in page.text
        assert page.text.index("library-back") < page.text.index("<h1")
        assert (
            "Импорт и источники" not in page.text
            and "Сценарии с Авиандром" not in page.text
        )
    for path in ("/medicines/import", "/medicines/import/1", "/medicines/scenarios"):
        assert client.get(path).status_code == 410
    catalogue = client.get("/medicines").text
    assert catalogue.index("medicine-add-entry") < catalogue.index(
        "data-catalog-search"
    )


def test_patient_csrf_and_unexpected_fields_cannot_create_a_drug(client):
    login(client, "doctor")
    data = form(client)
    assert (
        client.post("/medicines/new/save", data=data | {"csrf": "wrong"}).status_code
        == 403
    )
    assert (
        client.post("/medicines/new/save", data=data | {"code": "D01"}).status_code
        == 422
    )
    assert (
        client.post("/medicines/new/save", data=data | {"inn": ""}).status_code == 200
    )
    login(client, "patient1")
    assert client.get("/medicines/new").status_code == 403
    assert (
        client.post(
            "/medicines/new/save", data=data | {"csrf": token(client)}
        ).status_code
        == 403
    )


def test_html_labels_and_headings_remain_separate_and_unknown_fields_stay_empty():
    value, _ = medicine_web.parse(
        "<h1>Синтетическая карточка</h1><div>МНН: учебное вещество</div><h2>Показания</h2><p>Учебный текст.</p><h2>Противопоказания</h2><p>Другой текст.</p>"
    )
    assert value["inn"] == "учебное вещество"
    assert value["section_indications"] == "Учебный текст."
    assert "manufacturer" in value and value["manufacturer"] == ""
