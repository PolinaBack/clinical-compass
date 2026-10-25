"""An additive synthetic fixture must never replace cards, sources or AI results."""

import json
import os
from pathlib import Path

import pytest

from app.security import verify_password
from app.services import aviandr_analysis, aviandr_data, intake
from app.services.clinical_forms import visible
from app.storage import connect
from scripts import setup_absolut_patient as module
from scripts.absolut_scenario import USERNAME
from tests.test_aviandr_research import research, actor
from tests.test_aviandr_conclusions import snapshot, sign_in


def report(path, pid):
    with connect(path) as c:
        return aviandr_analysis.build(c, path, actor(c), pid)


def test_adds_complete_separate_card_and_preserves_every_previous_row(research):
    _, path, _, _ = research
    with connect(path) as c:
        before = snapshot(c)
    result = module.setup(path)
    assert result["created"] and result["doctor_id"] == 1
    with connect(path) as c:
        after = snapshot(c)
        for table, rows in before.items():
            assert not rows - after[table], table
        for table in (
            "aviandr_rules",
            "drug_imports",
            "drug_source_events",
            "aviandr_runs",
            "patient_ai_runs",
        ):
            assert after[table] == before[table]
        assert sum(after["patients"].values()) == sum(before["patients"].values()) + 1
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        sent = intake.read_submission(c, result["patient_id"])
        assert sent
        for section in sent["data"]["catalog"]["categories"]:
            answers = sent["data"]["answers"].get(section["key"], {})
            for field in section["fields"]:
                if visible(field, answers):
                    assert answers[field["key"]]["status"] == "answered", (
                        section["key"],
                        field["key"],
                    )
        assert len(sent["data"]["entities"]) == 8
    with connect(Path(result["backup"])) as c:
        assert snapshot(c) == before
    credentials = json.loads(
        Path(result["credentials_file"]).read_text(encoding="utf-8")
    )
    with connect(path) as c:
        row = c.execute(
            "SELECT * FROM accounts WHERE username=?", (USERNAME,)
        ).fetchone()
        assert verify_password(credentials["password"], row["password_hash"])
    if os.name != "nt":
        assert Path(result["credentials_file"]).stat().st_mode & 0o777 == 0o600


def test_actual_offline_calculation_has_all_four_directions_and_no_fake_sources(
    research,
):
    _, path, _, _ = research
    pid = module.setup(path)["patient_id"]
    value = report(path, pid)
    assert value["phase"] == "current"
    assert value["adherence"]["taken"] == 19
    assert value["adherence"]["missed"] == 1
    assert value["adherence"]["unknown"] == value["adherence"]["unrecorded"] == 0
    assert len([r for r in value["data"]["observations"] if r["diary_id"]]) == 30
    assert len(value["data"]["questionnaires"]) == 9
    assert {
        t["scale"]: [p["total"] for p in t["points"]] for t in value["trajectories"]
    } == {
        "phq4": [8, 6, 3],
        "gad7": [16, 12, 7],
        "phq9": [12, 9, 5],
    }
    assert len(value["data"]["preference_choices"]) == 3
    assert value["pairs"][0]["drug"]["code"] == "D02"
    assert not value["pairs"][0]["known"]
    assert not value["sources"] and value["rules_count"] == 0
    assert len(value["directions"]) == 4
    for direction in value["directions"]:
        assert len(direction["narrative"]["basis"]) == 3
        assert len(direction["narrative"]["questions"]) >= 2
        assert len(" ".join(direction["narrative"]["conclusion"])) > 100
    reaction = value["data"]["reactions"][0]
    assert (
        reaction["relation"] == "not_assessed"
    )  # Original patient report stays original.
    assert reaction["reviews"][0]["relation"] == "possible"
    assert reaction["reviews"][0]["patient_visible"]


def test_repeat_does_not_duplicate_refresh_dates_or_overwrite_manual_changes(research):
    _, path, _, _ = research
    first = module.setup(path)
    with connect(path) as c:
        c.execute(
            "UPDATE patients SET name='Абсолют: ручное изменение' WHERE id=?",
            (first["patient_id"],),
        )
        c.execute(
            "UPDATE diary SET note='Ручная новая запись' WHERE patient_id=?",
            (first["patient_id"],),
        )
        before = snapshot(c)
    second = module.setup(path)
    assert not second["created"] and second["patient_id"] == first["patient_id"]
    assert "credentials_file" not in second
    with connect(path) as c:
        assert snapshot(c) == before


@pytest.mark.parametrize("failure", ["mid-seed", "credentials", "occupied-file"])
def test_failure_rolls_back_without_changing_existing_cards(
    research, monkeypatch, failure
):
    _, path, _, _ = research
    credentials = path.parent / (path.name + ".absolut-credentials.json")
    if failure == "occupied-file":
        credentials.write_text("previous private file", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    if failure == "mid-seed":
        monkeypatch.setattr(module, "_observations", fail)
    elif failure == "credentials":
        monkeypatch.setattr(module.json, "dump", fail)
    with connect(path) as c:
        before = snapshot(c)
    with pytest.raises(OSError):
        module.setup(path)
    with connect(path) as c:
        assert snapshot(c) == before
    if failure == "occupied-file":
        assert credentials.read_text(encoding="utf-8") == "previous private file"
    else:
        assert not credentials.exists()


def test_scope_render_and_new_saved_fact_change_real_card(research):
    client, path, _, passwords = research
    pid = module.setup(path)["patient_id"]
    response = client.get(f"/patients/{pid}/ai-analysis")
    assert response.status_code == 200
    assert "Абсолют" in response.text and "Эсциталопрам" in response.text
    assert response.text.count('class="aviandr-direction"') == 4
    assert "Тестовый режим · только синтетические данные" in response.text
    before = report(path, pid)["checksum"]
    with connect(path) as c:
        aviandr_data.add_observation(
            c,
            path,
            actor(c),
            pid,
            kind="age",
            key="age",
            value=dict(value=37),
            confirmed=True,
            note="Новое синтетическое уточнение для проверки пересчёта",
        )
    assert report(path, pid)["checksum"] != before
    sign_in(client, "specialist-2", passwords["specialist-2"])
    assert client.get(f"/patients/{pid}/ai-analysis").status_code == 403


def test_missing_doctor_or_occupied_login_do_not_reuse_existing_patient(research):
    _, path, _, _ = research
    with connect(path) as c:
        before = snapshot(c)
    with pytest.raises(ValueError, match="Нет активного специалиста"):
        module.setup(path, doctor_id=9999)
    with connect(path) as c:
        assert snapshot(c) == before
        c.execute(
            "UPDATE accounts SET username=? WHERE username='patient-c'", (USERNAME,)
        )
        before = snapshot(c)
    with pytest.raises(ValueError, match="уже занят"):
        module.setup(path)
    with connect(path) as c:
        assert snapshot(c) == before


def test_missing_database_does_not_initialize_or_reset(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="База не найдена"):
        module.setup(path)
    assert not path.exists()
