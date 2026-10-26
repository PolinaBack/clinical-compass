"""Long imported/manual text is stored in full, without content-format gates."""

import json
from html import escape
from html.parser import HTMLParser

import httpx
import pytest

from app.services import drug_library as library, medicine_web
from app.storage import connect
from tests.helpers import login, token
from tests.test_doctor_medicines import form, transport


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
def test_large_multiline_values_save_verbatim_in_both_form_encodings(client, encoding):
    login(client, "doctor")
    long_section = "\n  BEGIN\n" + "Большой фрагмент текста.\n" * 50000 + "  END\n"
    assert len(long_section.encode()) > 1024 * 1024
    data = form(
        client,
        name="Пример",
        inn="",
        manufacturer="\n  " + "Производитель " * 200 + "\n",
        aliases="</textarea><script>notExecutable()</script>",
        section_adverse=long_section,
        source_url=" произвольное описание ресурса\nв любом формате ",
        source_text="ВЕСЬ ТЕКСТ\n" * 5000 + "LAST_SOURCE_LINE",
    )
    if encoding == "multipart":
        response = client.post(
            "/medicines/new/save", files=[(k, (None, str(v))) for k, v in data.items()]
        )
    else:
        response = client.post("/medicines/new/save", data=data)
    assert response.status_code == 200, response.text[:3000]
    code = response.url.path.rsplit("/", 1)[-1].upper()
    with connect(client.app.state.db_path) as c:
        drug = library.current(c, "drug")[code]
        raw = json.loads(
            c.execute(
                "SELECT content FROM drug_imports WHERE id=?", (drug["_import"],)
            ).fetchone()[0]
        )
        for key in (
            "name",
            "inn",
            "manufacturer",
            "aliases",
            "source_url",
            "source_text",
            "section_adverse",
        ):
            assert raw[key] == data[key], key
        assert drug["manufacturer"] == data["manufacturer"]
        assert drug["source_text"] == data["source_text"]
        assert (
            next(
                s
                for s in drug["reference_override"]["sections"]
                if s["key"] == "adverse"
            )["text"]
            == long_section
        )
    reopened = client.get(response.url.path)
    assert (
        reopened.status_code == 200
        and "LAST_SOURCE_LINE" in reopened.text
        and "  END\n" in reopened.text
    )
    assert 'href=" произвольное' not in reopened.text
    assert "<script>notExecutable()</script>" not in reopened.text


def test_long_values_survive_actual_reader_parse_preview_and_explicit_save(
    client, monkeypatch
):
    login(client, "doctor")
    manufacturer = "Производитель\n" * 90
    section = "Полный раздел. " * 3000 + "SECTION_LAST_WORD"
    group = "Учебная группа " * 80
    tail = "Сведения вне распознанных разделов. " * 1000 + "SOURCE_LAST_WORD"
    structured = json.dumps(
        dict(**{"@type": "Drug"}, name="Пример", manufacturer={"name": manufacturer}),
        ensure_ascii=False,
    )
    html = (
        '<script type="application/ld+json">'
        + structured
        + "</script><h1>Пример</h1><h2>Фармакотерапевтическая группа</h2><p>"
        + group
        + "</p><h2>Побочные действия</h2><p>"
        + section
        + "</p><h2>Другие сведения</h2><p>"
        + tail
        + "</p>"
    )
    original = medicine_web.fetch
    monkeypatch.setattr(
        medicine_web,
        "fetch",
        lambda url: original(
            url,
            transport=transport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/html; charset=utf-8"}, text=html
                )
            ),
        ),
    )
    preview = client.post(
        "/medicines/new/fetch",
        data=dict(csrf=token(client), source_url="https://example.org/long"),
    )
    assert preview.status_code == 200
    values = preview.context["values"]
    assert len(values["manufacturer"]) > 500
    assert len(values["section_adverse"]) > 15000 and values[
        "section_adverse"
    ].endswith("SECTION_LAST_WORD")
    assert len(values["group"]) > 300 and values["group"] == group.strip()
    assert len(values["source_text"]) > 20000 and values["source_text"].endswith(
        "SOURCE_LAST_WORD"
    )
    result = client.post(
        "/medicines/new/save",
        data=values
        | dict(csrf=token(client), client_token=preview.context["client_token"]),
    )
    assert result.status_code == 200
    with connect(client.app.state.db_path) as c:
        record = library.current(c, "drug")[result.url.path.rsplit("/", 1)[-1].upper()]
        assert record["manufacturer"] == values["manufacturer"]
        assert record["source_text"] == values["source_text"]
    assert "SOURCE_LAST_WORD" in result.text and "SECTION_LAST_WORD" in result.text


@pytest.mark.parametrize(
    "resource",
    ["javascript:alert(1)", "file:///пример", "https://example.org/" + "x" * 5000],
)
def test_resource_format_never_blocks_storage_and_unsafe_schemes_are_plain_text(
    client, resource
):
    login(client, "doctor")
    response = client.post(
        "/medicines/new/save", data=form(client, source_url=resource)
    )
    assert response.status_code == 200
    assert escape(resource, quote=False) in response.text
    if not resource.startswith("https://"):
        assert 'href="' + resource + '"' not in response.text


def test_save_editor_has_no_required_fields_or_length_caps_and_supports_multiline(
    client,
):
    login(client, "doctor")

    class Editor(HTMLParser):
        inside = False
        fields = {}

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "form" and values.get("action") == "/medicines/new/save":
                self.inside = True
            if self.inside and tag in ("input", "textarea"):
                self.fields[values.get("name")] = (tag, values)

        def handle_endtag(self, tag):
            if tag == "form":
                self.inside = False

    editor = Editor()
    editor.feed(client.get("/medicines/new").text)
    for key in (
        "name",
        "inn",
        "manufacturer",
        "section_adverse",
        "source_url",
        "source_text",
    ):
        tag, attributes = editor.fields[key]
        assert tag == "textarea"
        assert "maxlength" not in attributes and "required" not in attributes
