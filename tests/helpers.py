import re
import secrets
from app.security import hash_password
from app.storage import connect

TEST_PASSWORD = secrets.token_urlsafe(20)
TEST_HASH = hash_password(TEST_PASSWORD)


def provision(client):
    with connect(client.app.state.db_path) as c:
        for username, role, pid in [
            ("doctor", "doctor", None),
            ("patient1", "patient", 1),
            ("patient2", "patient", 2),
        ]:
            c.execute(
                "INSERT OR IGNORE INTO accounts(username,password_hash,role,patient_id,doctor_id) VALUES(?,?,?,?,1)",
                (username, TEST_HASH, role, pid),
            )


def token(client, path="/"):
    page = client.get(path)
    assert page.status_code == 200
    match = re.search(r'name="csrf"\s+value="([^"]+)"', page.text) or re.search(
        r'name="csrf-token"\s+content="([^"]+)"', page.text
    )
    assert match, "CSRF token not rendered"
    return match.group(1)


def login(client, role, consent=True):
    provision(client)
    if client.cookies.get("session"):
        csrf = token(client)
        client.post("/logout", data={"csrf": csrf})
    portal = "specialist" if role == "doctor" else "patient"
    response = client.post(
        "/login",
        data={
            "username": role,
            "password": TEST_PASSWORD,
            "portal": portal,
            "csrf": token(client, f"/login/{portal}"),
        },
    )
    assert response.status_code == 200
    if role != "doctor" and consent:
        result = client.post(
            "/consent",
            data={
                "csrf": token(client, "/consent"),
                "accepted": "true",
                "adult": "true",
            },
        )
        assert result.status_code == 200


def post(client, endpoint, **data):
    data.setdefault("csrf", token(client, "/patients/1"))
    return client.post(endpoint, data=data)
