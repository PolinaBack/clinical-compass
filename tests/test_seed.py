import sqlite3
import pytest
from scripts.seed_demo import seed


def test_synthetic_seed_repeat_preserves_existing_data(tmp_path):
    path = tmp_path / "demo.db"
    seed(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 3
        assert c.execute("SELECT urgent FROM risks").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM relatives").fetchone()[0] == 2
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(ValueError):
        seed(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == 3
