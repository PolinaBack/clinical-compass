"""Entry appearance stays separate from the signed-in cabinet styles."""

import re
import pytest
from tests.helpers import login, provision, token, TEST_PASSWORD


def assert_public(page):
    assert 'class="public-entry patient-space"' in page.text
    assert re.search(r'href="/static/style\.css\?', page.text)
    for name in (
        "interface.css",
        "diary.css",
        "clinician_workspace.css",
        "library_theme.css",
    ):
        assert f"/static/{name}" not in page.text
    assert "M32 8V56M8 32H56M15 15L49 49M49 15L15 49" in page.text
    assert 'class="portal-nav"' not in page.text


@pytest.mark.parametrize("path", ["/", "/login/patient", "/login/specialist"])
def test_original_entry_appearance_is_not_replaced_by_cabinet_styles(client, path):
    page = client.get(path)
    assert page.status_code == 200
    assert_public(page)
    assert 'href="/#resources"' in page.text
    if path == "/":
        assert 'href="/login/patient"' in page.text
        assert 'href="/login/specialist"' in page.text
    else:
        assert 'name="username"' in page.text and 'name="password"' in page.text


def test_existing_login_and_new_cabinet_remain_functional_after_public_restore(client):
    provision(client)
    csrf = token(client, "/login/specialist")
    data = dict(csrf=csrf, username="doctor", password="wrong", portal="specialist")
    failed = client.post("/login", data=data)
    assert failed.status_code == 401
    assert_public(failed)
    assert "Неверные данные или другой тип кабинета" in failed.text
    entered = client.post(
        "/login", data=data | {"password": TEST_PASSWORD}, follow_redirects=False
    )
    assert entered.status_code == 303 and entered.headers["location"] == "/doctor"
    library = client.get("/doctor/library")
    assert library.status_code == 200
    assert 'class="portal-shell signed-in specialist-space' in library.text
    assert "/static/library_theme.css?" in library.text
    assert "library-entry-grid" in library.text
    assert "medicine-add-entry" in client.get("/medicines").text
    assert_public(client.get("/"))
    assert_public(client.get("/login/patient"))
    assert client.get("/doctor").status_code == 200


def test_patient_still_enters_own_current_cabinet(client):
    login(client, "patient1")
    page = client.get("/patients/1/diary")
    assert page.status_code == 200
    assert 'class="portal-shell signed-in patient-space' in page.text
    assert "/static/interface.css?" in page.text
    assert client.get("/patients/2/diary").status_code == 403
