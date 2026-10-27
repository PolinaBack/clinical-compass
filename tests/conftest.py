import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as c:
        yield c


@pytest.fixture
def verified_test_medicines(client):
    """Synthetic prerequisites for older UI tests that intentionally add D01/D09."""
    from tests.helpers import provision
    from tests.medicine_verification_helpers import verify
    from app.storage import connect

    provision(client)
    with connect(client.app.state.db_path) as c:
        doctor = c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
        for code in ("D01", "D09"):
            verify(c, doctor, code)
