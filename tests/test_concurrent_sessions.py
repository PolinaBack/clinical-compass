"""Independent browsers may share an account, but never each other's CSRF token."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.session_keys import session_key_path, session_secret
from app.storage import connect
from tests.helpers import login, token


@pytest.fixture(autouse=True)
def isolated_session_configuration(monkeypatch):
    monkeypatch.delenv("COMPASS_SESSION_SECRET", raising=False)
    monkeypatch.delenv("COMPASS_SESSION_KEY_FILE", raising=False)


def prepare_comment(client):
    with connect(client.app.state.db_path) as c:
        return c.execute(
            "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(1,'2026-10-25',5,8,'none','Синтетическая запись',0)"
        ).lastrowid


@pytest.mark.parametrize("role", ["doctor", "patient1"])
def test_same_account_has_independent_concurrent_browsers_and_local_logout(
    tmp_path, role
):
    app = create_app(tmp_path / "shared.db")
    with TestClient(app) as first, TestClient(app) as second:
        login(first, role)
        first_csrf = token(first, "/settings")
        diary_id = prepare_comment(first)
        login(second, role)
        second_csrf = token(second, "/settings")
        assert first_csrf != second_csrf
        endpoint = (
            f"/patients/1/diary/{diary_id}/comments"
            if role == "doctor"
            else "/patients/1/notes"
        )
        routes = (
            ("/doctor", "/doctor/calendar", "/doctor/library", "/settings")
            if role == "doctor"
            else (
                "/patients/1/diary",
                "/patients/1/profile",
                "/patients/1/results",
                "/settings",
            )
        )
        for page in routes:
            assert first.get(page).status_code == 200, page
            assert second.get(page).status_code == 200, page

        # A second login must not invalidate a form opened in the first browser.
        def save(client, csrf, body):
            return client.post(endpoint, data=dict(csrf=csrf, body=body)).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    save, first, first_csrf, "Синтетический ввод первого браузера"
                ),
                pool.submit(
                    save, second, second_csrf, "Синтетический ввод второго браузера"
                ),
            ]
            assert [f.result() for f in futures] == [200, 200]
        assert save(second, first_csrf, "Чужой токен") == 403
        assert first.post("/logout", data=dict(csrf=first_csrf)).status_code == 200
        assert first.get("/settings").status_code == 401
        assert second.get("/settings").status_code == 200
        assert (
            save(second, second_csrf, "Синтетический ввод после выхода первого") == 200
        )
        assert second.post("/logout", data=dict(csrf=second_csrf)).status_code == 200
        with connect(app.state.db_path) as c:
            table = "diary_comments" if role == "doctor" else "diary_notes"
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 3


def test_cookie_and_open_form_work_across_workers_and_restart(tmp_path):
    path = tmp_path / "shared.db"
    with ExitStack() as stack:
        first = stack.enter_context(TestClient(create_app(path)))
        second = stack.enter_context(TestClient(create_app(path)))
        login(first, "patient1")
        csrf = token(first, "/patients/1/diary")
        second.cookies.update(first.cookies)
        assert second.get("/patients/1/profile").status_code == 200
        assert (
            second.post(
                "/patients/1/notes",
                data=dict(csrf=csrf, body="Синтетическое действие другого процесса"),
            ).status_code
            == 200
        )
        saved_cookies = dict(second.cookies)
    with TestClient(create_app(path)) as restarted:
        restarted.cookies.update(saved_cookies)
        assert restarted.get("/patients/1/results").status_code == 200
        assert (
            restarted.post(
                "/patients/1/notes",
                data=dict(csrf=csrf, body="Синтетическое действие после перезапуска"),
            ).status_code
            == 200
        )
        assert restarted.post("/logout", data=dict(csrf=csrf)).status_code == 200


def test_logout_of_an_already_closed_browser_session_is_idempotent(client):
    login(client, "patient1")
    csrf = token(client, "/settings")
    assert client.post("/logout", data=dict(csrf=csrf)).status_code == 200
    # Back/cached tab, or a form from an expired cookie, has nothing left to close.
    assert client.post("/logout", data=dict(csrf=csrf)).status_code == 200
    assert client.get("/settings").status_code == 401


def test_bad_logout_token_still_cannot_close_an_authenticated_session(client):
    login(client, "doctor")
    assert client.post("/logout", data=dict(csrf="not-this-browser")).status_code == 403
    assert client.get("/doctor").status_code == 200
    assert client.post("/logout").status_code == 403
    assert client.get("/doctor").status_code == 200


def test_parallel_first_workers_publish_one_complete_private_key(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPASS_SESSION_SECRET", raising=False)
    monkeypatch.delenv("COMPASS_SESSION_KEY_FILE", raising=False)
    path = tmp_path / "shared.db"
    with ThreadPoolExecutor(max_workers=12) as pool:
        keys = list(pool.map(lambda _: session_secret(path), range(36)))
    assert len(set(keys)) == 1
    key_path = session_key_path(path)
    assert key_path.read_text().strip() == keys[0]
    assert not list(tmp_path.glob(".session-key-*"))
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600
    assert session_secret(tmp_path / "different.db") != keys[0]


def test_configured_key_is_stable_and_invalid_key_is_not_replaced(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("COMPASS_SESSION_SECRET", raising=False)
    key_path = tmp_path / "private" / "configured.session.key"
    monkeypatch.setenv("COMPASS_SESSION_KEY_FILE", str(key_path))
    original = session_secret(tmp_path / "a.db")
    assert session_secret(tmp_path / "a.db") == original
    key_path.write_bytes(b"incomplete")
    with pytest.raises(RuntimeError, match="не перезаписан"):
        session_secret(tmp_path / "a.db")
    assert key_path.read_bytes() == b"incomplete"
    monkeypatch.setenv("COMPASS_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="32"):
        session_secret(tmp_path / "a.db")
    monkeypatch.setenv("COMPASS_SESSION_SECRET", "x" * 64)
    assert session_secret(tmp_path / "a.db") == "x" * 64


def test_password_revocation_still_ends_all_browsers(tmp_path):
    path = tmp_path / "shared.db"
    with TestClient(create_app(path)) as first, TestClient(create_app(path)) as second:
        login(first, "patient1")
        login(second, "patient1")
        csrf = token(first, "/settings")
        with connect(path) as c:
            c.execute(
                "UPDATE accounts SET auth_version=auth_version+1 WHERE username='patient1'"
            )
        for client in (first, second):
            assert client.get("/patients/1/diary").status_code == 401
            assert (
                client.post(
                    "/patients/1/notes", data=dict(csrf=csrf, body="Сеанс уже отозван")
                ).status_code
                == 401
            )


def test_foreign_database_and_forged_cookie_cannot_reuse_login(tmp_path):
    with TestClient(create_app(tmp_path / "one.db")) as first, TestClient(
        create_app(tmp_path / "two.db")
    ) as other:
        login(first, "doctor")
        login(other, "doctor")
        cookie = dict(first.cookies)
        other.cookies.clear()
        other.cookies.update(cookie)
        assert other.get("/doctor").status_code == 401
        value = cookie["session"]
        first.cookies.clear()
        first.cookies.set("session", ("A" if value[0] != "A" else "B") + value[1:])
        assert first.get("/doctor").status_code == 401
