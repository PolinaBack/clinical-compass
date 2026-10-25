"""Explicit, additive repair of the old reset that reused A/B for C/D."""

from pathlib import Path
import sys

import pytest

from app.services.patient_ai_data import scope
from app.storage import connect
from scripts.reassign_demo_patients import organize
from scripts import setup_aviandr_scenarios as scenarios
from tests.test_aviandr_research import research, actor
from tests.test_aviandr_conclusions import snapshot
from fastapi import HTTPException


def test_restore_ab_from_legacy_login_reuse_keeps_all_scenario_history(research):
    _, path, pids, _ = research
    path = path.parent / "legacy-reset.db"
    scenarios.setup(path, reset=True)
    with connect(path) as c:
        pids = {
            r["scenario"]: r["patient_id"]
            for r in c.execute("SELECT * FROM aviandr_scenarios")
        }
    # Reproduce the exact old --reset mapping: patient-a/b still own C/D.
    with connect(path) as c:
        c.execute(
            "UPDATE accounts SET username='patient-a' WHERE role='patient' AND patient_id=?",
            (pids["C"],),
        )
        c.execute(
            "UPDATE accounts SET username='patient-b' WHERE role='patient' AND patient_id=?",
            (pids["D"],),
        )
        hashes = {
            r["username"]: r["password_hash"]
            for r in c.execute("SELECT * FROM accounts")
        }
        before = snapshot(c)
        versions = {
            r["username"]: r["auth_version"]
            for r in c.execute("SELECT * FROM accounts")
        }
    result = organize(path)
    assert Path(result["backup"]).exists()
    assigned = {r["patient"]: r for r in result["assignments"]}
    assert assigned["A"]["patient_id"] not in pids.values()
    assert assigned["B"]["patient_id"] not in pids.values()
    assert (
        assigned["C"]["patient_id"] == pids["C"]
        and assigned["D"]["patient_id"] == pids["D"]
    )
    assert {r["patient"]: r["doctor_id"] for r in result["assignments"]} == dict(
        A=1, B=1, C=2, D=2
    )
    assert {name for name, _ in result["credentials"]} == {"patient-c", "patient-d"}
    with connect(path) as c:
        after = snapshot(c)
        for table, rows in before.items():
            if table not in {"patients", "accounts"}:
                assert after[table] == rows, table
        for row in c.execute("SELECT * FROM accounts"):
            if row["username"] in hashes:
                assert row["password_hash"] == hashes[row["username"]]
            if row["username"] in {"patient-a", "patient-b"}:
                assert row["auth_version"] == versions[row["username"]] + 1
        with pytest.raises(HTTPException):
            scope(c, actor(c)["id"], pids["C"])
        scope(c, actor(c, 2)["id"], pids["C"])
        scope(c, actor(c)["id"], assigned["A"]["patient_id"])
        with pytest.raises(HTTPException):
            scope(c, actor(c, 2)["id"], assigned["A"]["patient_id"])
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        stable = snapshot(c)
    again = organize(path)
    assert not again["credentials"] and again["assignments"] == result["assignments"]
    with connect(path) as c:
        assert snapshot(c) == stable


def test_existing_ab_patients_are_reused_instead_of_duplicated(research):
    _, path, pids, _ = research
    with connect(path) as c:
        a, b = 1, 2
        c.execute("UPDATE patients SET doctor_id=2 WHERE id IN (1,2)")
        before = c.execute("SELECT count(*) FROM patients").fetchone()[0]
    result = organize(path)
    assert {r["patient"]: r["patient_id"] for r in result["assignments"]} == dict(
        A=a, B=b, C=pids["C"], D=pids["D"]
    )
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM patients").fetchone()[0] == before
        assert (
            c.execute(
                "SELECT doctor_id FROM patients WHERE id=?", (pids["E"],)
            ).fetchone()[0]
            == 2
        )


def test_ambiguous_login_rolls_back_every_assignment(research):
    _, path, _, _ = research
    with connect(path) as c:
        c.execute(
            "UPDATE accounts SET username='patient-b' WHERE role='doctor' AND doctor_id=2"
        )
        before = snapshot(c)
    with pytest.raises(ValueError):
        organize(path)
    with connect(path) as c:
        assert snapshot(c) == before


def test_setup_cli_applies_new_roster_without_reset_or_duplicate_scenarios(
    research, monkeypatch, capsys
):
    _, path, _, _ = research
    with connect(path) as c:
        before = c.execute("SELECT count(*) FROM diary").fetchone()[0]
    monkeypatch.setattr(sys, "argv", ["setup_aviandr_scenarios", "--db", str(path)])
    scenarios.main()
    output = capsys.readouterr().out
    assert "C/D — за специалистом 2" in output
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM aviandr_scenarios").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM diary").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM patients").fetchone()[0] == 5
        hashes = {
            r["username"]: r["password_hash"]
            for r in c.execute("SELECT * FROM accounts")
        }
    scenarios.main()
    capsys.readouterr()
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM patients").fetchone()[0] == 5
        assert hashes == {
            r["username"]: r["password_hash"]
            for r in c.execute("SELECT * FROM accounts")
        }
