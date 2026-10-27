"""Technical acceptance on fictional cards, not evidence of medical validity."""

import re
from collections import Counter
from itertools import combinations
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData
from app.main import create_app
from app.services import (
    drug_library as library,
    medicine_drafts as drafts,
    medicine_reference as reference,
)
from app.storage import connect
from scripts.backup import backup
from tests.helpers import login, token, TEST_HASH


def scheme(client, pid=1, revision=0, codes=("D01",), **extra):
    # These legacy scheme tests start with explicitly verified synthetic medicines.
    # Blocking of unverified medicines has its own tests without this helper.
    from tests.medicine_verification_helpers import verify

    with connect(client.app.state.db_path) as c:
        user = c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        if user:
            for code in codes:
                verify(c, user, code)
    result = dict(
        csrf=token(client, "/medicines/prescriptions"),
        patient_id=str(pid),
        revision=str(revision),
        intent="save",
        drug=list(codes),
    )
    for code in codes:
        result.update(
            {code + ".dose": "СИНТЕТИЧЕСКАЯ ДОЗА", code + ".frequency": "1 раз в день"}
        )
    result.update(extra)
    return result


def saved(client, pid=1):
    with connect(client.app.state.db_path) as c:
        user = c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        return drafts.read(c, client.app.state.db_path, user, pid)


def test_reference_complete_and_no_fake_frequency_or_individual_rule():
    data = reference.data()
    assert set(data["cards"]) == {f"D{i:02}" for i in range(1, 13)}
    assert sum(len(d["reactions"]) for d in data["cards"].values()) == 468
    assert set(data["pairs"]) == {
        "|".join(p) for p in combinations(sorted(data["cards"]), 2)
    }
    assert Counter(p["level"] for p in data["pairs"].values()) == {
        "ПРОТИВОПОКАЗАНО": 3,
        "ПОВЫШЕННЫЙ РИСК": 5,
        "ОЦЕНИТЬ": 41,
        "НЕТ ДАННЫХ": 17,
    }
    assert sum(p["basis"] == "editorial_rule" for p in data["pairs"].values()) == 21
    for code, card in data["cards"].items():
        assert len(card["sections"]) == 13 and all(s["text"] for s in card["sections"])
        assert len({(r["system"], r["term"]) for r in card["reactions"]}) == len(
            card["reactions"]
        )
        assert all(library.safe_url(url) for url in card["links"])
    assert "эвристика" in reference.pair("D02", "D04")["origin"]
    assert reference.pair("D03", "D09")["level"] == "unknown"
    assert reference.pair("D99", "D01")["level"] == "unknown"


def test_catalogue_reference_and_archives_and_requested_no_specification_citation(
    client,
):
    login(client, "doctor")
    with connect(client.app.state.db_path) as c:
        original = library.current(c, "drug")["D02"]
        assert original["name"] == "Ципралекс"  # original version is not overwritten
        assert library.search(c, "пранафарм")[0]["name"] == "Эсциталопрам"
        assert len(library.search(c, "СИОЗС")) >= 4
        assert library.search(c, "серлифт")[0]["code"] == "D03"
    for code in reference.data()["cards"]:
        page = client.get("/medicines/" + code.lower())
        assert page.status_code == 200
        assert 'class="card medicine-toc"' in page.text
        assert reference.card(code)["discrepancy"] in page.text
        assert "Клинический_компас_v10" not in page.text
        assert "справочник_и_назначения.pdf" not in page.text
        assert "Неподтверждённые исходные данные Excel" in page.text
        assert "не полная официальная инструкция" in page.text
    html = client.get("/medicines").text
    names = re.findall(r"<h2><a[^>]*>([^<]+)</a></h2>", html)
    assert names == sorted(names, key=library.normalize) and len(names) == 12
    assert html.count('class="card medicine-product"') == 12


def test_selection_stays_in_place_and_is_independent_of_drafts(client):
    login(client, "doctor")
    csrf = token(client, "/medicines")
    response = client.post(
        "/medicines/combinations/selection",
        data=dict(csrf=csrf, action="add", code="D01", return_to="/medicines"),
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/medicines"
    assert "В проверке: 1" in client.get("/medicines").text
    for selected, code in [(["D01"], "D02"), (["D01", "D02"], "D03")]:
        response = client.post(
            "/medicines/combinations/selection",
            data=dict(
                csrf=csrf,
                selected=selected,
                action="add",
                code=code,
                return_to="/medicines/combinations",
                picker="1",
            ),
            follow_redirects=False,
        )
        assert response.headers["location"].endswith("picker=1#medicine-picker")
    html = client.get(response.headers["location"]).text
    assert "Проверено пар: 3." in html and 'id="medicine-picker" open' in html
    assert (
        "Проверено пар: 6."
        in client.get(
            "/medicines/combinations?drug=D01&drug=D02&drug=D03&drug=D04"
        ).text
    )
    assert "Проверено пар: 66." in client.get("/medicines/combinations?all=1").text
    target = "/medicines/combinations?drug=D01&drug=D02&drug=D03"
    html = client.get("/medicines/d02?" + urlencode(dict(return_to=target))).text
    assert 'href="/medicines/combinations?drug=D01&amp;drug=D02&amp;drug=D03"' in html
    assert saved(client)["items"] == []
    # Comparison scoped to a draft must not replace the general set.
    client.post(
        "/medicines/combinations/selection",
        data=dict(
            csrf=csrf,
            selected=["D05"],
            action="add",
            code="D09",
            draft_patient="1",
            return_to="/medicines/combinations",
        ),
    )
    assert "Проверено пар: 3." in client.get("/medicines/combinations").text
    response = client.post(
        "/medicines/combinations/selection",
        data=dict(
            csrf=csrf, action="add", code="D01", return_to="https://attacker.invalid"
        ),
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/medicines/combinations")


@pytest.mark.parametrize(
    "changes",
    [
        {"D01.dose": ""},
        {"D01.frequency": ""},
        {"D01.frequency": "системой подобрано"},
        {"D01.frequency": "Другая схема", "D01.frequency_other": ""},
        {"D01.food": "Другое", "D01.food_other": ""},
        {"D01.route": "Другой способ", "D01.route_other": ""},
        {"D01.start": "2026-02-30"},
        {"D01.comment": "x" * 3001},
        {"drug": []},
        {"drug": ["D99"]},
        {"drug": ["D01", "D01"]},
    ],
)
def test_invalid_scheme_never_saves_partial_draft(client, changes):
    login(client, "doctor")
    response = client.post(
        "/medicines/prescriptions/save", data=scheme(client, **changes)
    )
    assert response.status_code == 422
    assert saved(client)["items"] == []


def test_drafts_are_encrypted_versioned_per_patient_and_do_not_change_orders(
    client, tmp_path
):
    login(client, "doctor")
    form = scheme(
        client,
        codes=("D01", "D09"),
        **{
            "D01.times": "08:15, 20:15",
            "D01.food": "Другое",
            "D01.food_other": "СИНТЕТИЧЕСКОЕ УТОЧНЕНИЕ",
            "D01.route": "Внутрь",
            "D01.start": "2026-11-02",
            "D01.duration": "СИНТЕТИЧЕСКИЙ КУРС",
            "D01.comment": '<script>alert("XSS")</script>',
        },
    )
    with connect(client.app.state.db_path) as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in (
                "decisions",
                "resource_assignments",
                "diary",
                "intake_submissions",
            )
        }
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    item = saved(client)["items"][0]
    assert (
        item["times"] == "08:15, 20:15"
        and item["reference"]["registration"]
        and item["reference"]["status"] == "rf_instruction_verified"
    )
    assert saved(client, 2)["items"] == []
    assert (
        "СИНТЕТИЧЕСКАЯ ДОЗА"
        not in client.get("/medicines/prescriptions?patient_id=2").text
    )
    with connect(client.app.state.db_path) as c:
        encrypted = c.execute("SELECT payload FROM medicine_drafts").fetchone()[0]
        assert (
            b"XSS" not in encrypted and "СИНТЕТИЧЕСКАЯ ДОЗА".encode() not in encrypted
        )
        assert counts == {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts
        }
    html = client.get("/medicines/prescriptions?patient_id=1").text
    assert "&lt;script&gt;alert" in html and '<script>alert("XSS")</script>' not in html
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 409
    form["revision"] = "1"
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    assert saved(client)["revision"] == 1  # identical data does not append a version
    form["D01.dose"] = "СИНТЕТИЧЕСКАЯ ИЗМЕНЁННАЯ ДОЗА"
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    assert saved(client)["revision"] == 2
    old = client.get("/medicines/prescriptions?patient_id=1&version=1")
    assert (
        "СИНТЕТИЧЕСКАЯ ИЗМЕНЁННАЯ ДОЗА" not in old.text
        and 'data-readonly="1"' in old.text
    )
    assert (
        client.get("/medicines/prescriptions?patient_id=1&version=99").status_code
        == 404
    )
    copied = tmp_path / "restored.db"
    backup(client.app.state.db_path, copied)
    assert Path(str(copied) + ".key").is_file()
    with TestClient(create_app(copied)) as restored:
        login(restored, "doctor")
        assert saved(restored)["items"][0]["dose"] == "СИНТЕТИЧЕСКАЯ ИЗМЕНЁННАЯ ДОЗА"
    form = scheme(client, revision=2, codes=("D01", "D09"), intent="check")
    response = client.post(
        "/medicines/prescriptions/save", data=form, follow_redirects=False
    )
    assert (
        response.status_code == 303
        and "drug=D01&drug=D09&draft_patient=1" in response.headers["location"]
    )


def test_draft_access_missing_csrf_key_loss_and_patient_cannot_read(client):
    login(client, "doctor")
    form = scheme(client)
    csrf = form.pop("csrf")
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 403
    form["csrf"] = csrf
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    key = client.app.state.db_path.parent / "attachments.key"
    key.unlink()  # simulated loss only in disposable test DB
    response = client.get("/medicines/prescriptions?patient_id=1")
    assert response.status_code == 503 and not key.exists()
    login(client, "patient1")
    for url in (
        "/medicines/prescriptions?patient_id=1",
        "/medicines/combinations?draft_patient=1",
    ):
        assert client.get(url).status_code == 403
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 403


def test_draft_foreign_doctor_cannot_read_or_write(client):
    login(client, "doctor")
    form = scheme(client)
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    with connect(client.app.state.db_path) as c:
        c.execute("INSERT INTO doctors(id,name) VALUES(9,'Другой синтетический врач')")
        c.execute("UPDATE accounts SET doctor_id=9 WHERE username='doctor'")
    assert client.get("/medicines/prescriptions?patient_id=1").status_code == 403
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 403
    assert (
        client.get(
            "/medicines/combinations?drug=D01&drug=D02&draft_patient=1"
        ).status_code
        == 403
    )


def test_draft_is_private_to_author_even_with_same_doctor_scope(client):
    login(client, "doctor")
    form = scheme(client)
    assert client.post("/medicines/prescriptions/save", data=form).status_code == 200
    with connect(client.app.state.db_path) as c:
        ident = c.execute(
            "INSERT INTO accounts(username,password_hash,role,doctor_id) VALUES(?,?,'doctor',1)",
            ("other-synthetic-author", TEST_HASH),
        ).lastrowid
        other = c.execute("SELECT * FROM accounts WHERE id=?", (ident,)).fetchone()
        assert drafts.read(c, client.app.state.db_path, other, 1)["items"] == []
        drafts.save(
            c,
            client.app.state.db_path,
            other,
            1,
            FormData({**form, "drug": "D01", "D01.dose": "ДОЗА ДРУГОГО АВТОРА"}),
        )
        assert (
            c.execute(
                "SELECT count(*) FROM medicine_drafts WHERE patient_id=1"
            ).fetchone()[0]
            == 2
        )
    assert saved(client)["items"][0]["dose"] == "СИНТЕТИЧЕСКАЯ ДОЗА"


def test_card_actions_keep_the_explicit_comparison_and_draft_context(client):
    login(client, "doctor")
    target = "/medicines/combinations?drug=D05&drug=D09&draft_patient=1"
    html = client.get("/medicines/d11?" + urlencode(dict(return_to=target))).text
    assert (
        'href="/medicines/combinations?drug=D05&amp;drug=D09&amp;draft_patient=1">Проверить сочетания'
        in html
    )
    assert (
        'name="selected" value="D05"' in html and 'name="selected" value="D09"' in html
    )
    assert 'name="draft_patient" value="1"' in html
    csrf = token(client, "/medicines")
    response = client.post(
        "/medicines/combinations/selection",
        data=dict(
            csrf=csrf,
            selected=["D05", "D09"],
            action="add",
            code="D11",
            draft_patient="1",
            return_to=target,
        ),
        follow_redirects=False,
    )
    assert "drug=D05&drug=D09&drug=D11&draft_patient=1" in response.headers["location"]
    assert "Проверено пар: 3." in client.get(response.headers["location"]).text
    assert saved(client)["items"] == []
