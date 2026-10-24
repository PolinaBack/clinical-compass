"""Reset is explicit, demo-only, backed up, atomic and leaves unrelated records."""

from datetime import datetime, timezone
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.storage import connect, now
from app.intake_catalog import VERSION
from app.services.clinical_attachments import cipher
from tests.helpers import login
from scripts.reset_demo_forms import reset


def fill(client):
    login(client, "doctor")
    path = client.app.state.db_path
    with connect(path) as c:
        actor = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[
            0
        ]
        c.execute(
            "INSERT INTO patients(id,name,doctor_id) VALUES(3,'Сохраняемый профиль',1)"
        )
        identifiers = {}
        for pid in (1, 2, 3):
            sid = c.execute(
                "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,'2026-10-01','2026-10-14',?)",
                (pid, now()),
            ).lastrowid
            aid = c.execute(
                "INSERT INTO assessments(session_id,patient_id,scale,version,status,completed_at,seen_at,revision) VALUES(?,?,'phq4','test','completed',?,?,4)",
                (sid, pid, now(), now()),
            ).lastrowid
            answer = c.execute(
                "INSERT INTO answers(assessment_id,item,value,state,comment,changed_at) VALUES(?,'1',2,'answered','История ответа',?)",
                (aid, now()),
            ).lastrowid
            c.execute(
                "INSERT INTO answer_history(answer_id,payload,account_id,created_at) VALUES(?,?,?,?)",
                (answer, '{"old":true}', actor, now()),
            )
            c.execute(
                "INSERT INTO assessment_events(patient_id,assessment_id,kind,created_at) VALUES(?,?,'test',?)",
                (pid, aid, now()),
            )
            c.execute(
                "INSERT INTO save_requests VALUES(?,?,?)",
                (f"save-{pid}", actor, json.dumps(dict(assessment=aid, answers=[2]))),
            )
            c.execute(
                "INSERT INTO video_choices VALUES(?,?,'accepted','test',?,?)",
                (aid, pid, now(), now()),
            )
            c.execute(
                "INSERT INTO audio_notes(patient_id,assessment_id,item,file_name,mime,duration,consent_at,created_at,content) VALUES(?,?,'1','private-audio','audio/wav',1,?,?,?)",
                (pid, aid, now(), now(), b"SYNTHETIC AUDIO"),
            )
            c.execute(
                "INSERT INTO video_recordings(assessment_id,patient_id,mime,duration,consent_at,created_at,content,upload_id) VALUES(?,?,'video/mp4',1,?,?,?,?)",
                (aid, pid, now(), now(), b"SYNTHETIC VIDEO", f"video-{pid}"),
            )
            c.execute(
                "INSERT INTO intake_drafts VALUES(?,3,?,?,?)",
                (
                    pid,
                    VERSION,
                    '{"complaints":{"comment":{"status":"answered","value":"Синтетические ответы"}}}',
                    now(),
                ),
            )
            sub = c.execute(
                "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(?,3,?,?,?,?)",
                (pid, VERSION, '{"answers":{}}', actor, now()),
            ).lastrowid
            c.execute(
                "INSERT INTO intake_reviews(submission_id,target,status,note,actor_id,created_at) VALUES(?,'field','clarify','Синтетическое уточнение',?,?)",
                (sub, actor, now()),
            )
            entity = c.execute(
                "INSERT INTO intake_entities(patient_id,kind,version,data_json,updated_by,updated_at) VALUES(?,'condition',1,'{}',?,?)",
                (pid, actor, now()),
            ).lastrowid
            entity2 = c.execute(
                "INSERT INTO intake_entities(patient_id,kind,version,data_json,updated_by,updated_at) VALUES(?,'visit',1,'{}',?,?)",
                (pid, actor, now()),
            ).lastrowid
            c.execute(
                "INSERT INTO intake_entity_links VALUES(?,?,?,?,?)",
                (pid, entity, entity2, actor, now()),
            )
            c.execute(
                "INSERT INTO intake_changes(patient_id,actor_id,kind,revision,payload,created_at) VALUES(?,?,'save',3,'{}',?)",
                (pid, actor, now()),
            )
            c.execute(
                "INSERT INTO patient_profiles VALUES(?,'Старое обо мне','','','','','',?)",
                (pid, now()),
            )
            c.execute(
                "INSERT INTO screenings(patient_id,day,tension,difficulty,note) VALUES(?,'2026-10-01',2,3,'Синтетический старый самоотчёт')",
                (pid,),
            )
            key = cipher(path, c)
            c.execute(
                "INSERT INTO clinical_attachments(patient_id,owner_id,scope,category,entity_id,filename,mime,byte_size,sha256,content,scan_status,scan_engine,created_at) VALUES(?,?,'intake','health',?,'synthetic.pdf','application/pdf',4,'test',?,'not_scanned','',?)",
                (pid, actor, entity, key.encrypt(b"SYNTHETIC DOCUMENT"), now()),
            )
            c.execute(
                "INSERT INTO clinical_attachments(patient_id,owner_id,scope,category,filename,mime,byte_size,sha256,content,scan_status,scan_engine,created_at) VALUES(?,?,'diary','','synthetic.pdf','application/pdf',4,'test',?,'not_scanned','',?)",
                (pid, actor, key.encrypt(b"SYNTHETIC DIARY FILE"), now()),
            )
            c.execute(
                "INSERT INTO patient_ai_runs(patient_id,actor_id,actor_auth_version,status,stage,snapshot_hash,snapshot,result,created_at) VALUES(?,?,1,'completed','completed','test',?,?,?)",
                (
                    pid,
                    actor,
                    key.encrypt(b"SYNTHETIC SNAPSHOT"),
                    key.encrypt(b"SYNTHETIC RESULT"),
                    now(),
                ),
            )
            c.execute(
                "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(?,'2026-10-01',5,7,'none','KEEP DIARY',0)",
                (pid,),
            )
            c.execute(
                "INSERT INTO messages(patient_id,author,body,day) VALUES(?,'Пациент','KEEP MESSAGE','2026-10-01')",
                (pid,),
            )
            c.execute(
                "INSERT INTO relatives(patient_id,name,relation) VALUES(?,'KEEP FAMILY','sister')",
                (pid,),
            )
            identifiers[pid] = (aid, entity)
    return identifiers


def snapshot(path):
    with sqlite3.connect(path) as c:
        names = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: sorted(c.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
            for name in names
        }


def test_preview_apply_both_demo_forms_preserves_other_data_and_restore(
    client, tmp_path
):
    ids = fill(client)
    path = client.app.state.db_path
    before = snapshot(path)
    preview = reset(path)
    assert (
        not preview["changed"]
        and preview["records"]["answers"] == 2
        and preview["records"]["intake_drafts"] == 2
    )
    assert snapshot(path) == before
    saved = tmp_path / "reset-backup.db"
    result = reset(path, apply=True, destination=saved)
    assert (
        result["changed"]
        and saved.is_file()
        and (tmp_path / "reset-backup.db.key").is_file()
    )
    assert snapshot(saved) == before
    after = snapshot(path)
    for name in [
        "patients",
        "doctors",
        "accounts",
        "consents",
        "consent_events",
        "diary",
        "messages",
        "relatives",
        "account_preferences",
        "assessment_sessions",
    ]:
        if name in before:
            assert before[name] == after[name], name
    with connect(path) as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in [
            "intake_drafts",
            "intake_submissions",
            "intake_entities",
            "intake_changes",
            "intake_reviews",
            "patient_profiles",
            "screenings",
            "answers",
            "answer_history",
            "audio_notes",
            "video_recordings",
            "video_choices",
            "assessment_events",
            "patient_ai_runs",
        ]:
            assert c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == (
                2 if table == "intake_entities" else 1
            ), table
        for pid in (1, 2):
            a = c.execute(
                "SELECT * FROM assessments WHERE id=?", (ids[pid][0],)
            ).fetchone()
            assert (
                a["status"] == "draft"
                and a["revision"] == 5
                and a["completed_at"] is None
                and a["seen_at"] is None
            )
        assert (
            c.execute(
                "SELECT status FROM assessments WHERE id=?", (ids[3][0],)
            ).fetchone()[0]
            == "completed"
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM clinical_attachments WHERE scope='diary'"
            ).fetchone()[0]
            == 3
        )
        assert c.execute("SELECT token FROM save_requests").fetchone()[0] == "save-3"
        encrypted = c.execute(
            "SELECT content FROM clinical_attachments WHERE scope='diary' LIMIT 1"
        ).fetchone()[0]
        assert cipher(path, c).decrypt(encrypted) == b"SYNTHETIC DIARY FILE"
    assert not reset(path, apply=True, destination=tmp_path / "unused.db")["changed"]
    assert not (tmp_path / "unused.db").exists()
    assert client.get("/patients/1/profile/about").status_code == 200
    assert client.get(f"/assessments/{ids[1][0]}").status_code == 200


@pytest.mark.parametrize("change", ["rename", "account", "foreign-id"])
def test_identity_or_unauthorized_target_refuses_everything(client, tmp_path, change):
    fill(client)
    path = client.app.state.db_path
    with connect(path) as c:
        if change == "rename":
            c.execute("UPDATE patients SET name='Переименованная карта' WHERE id=2")
        if change == "account":
            c.execute(
                "UPDATE accounts SET username='different-user' WHERE patient_id=2"
            )
    before = snapshot(path)
    with pytest.raises(ValueError):
        reset(
            path,
            pids=(1, 3) if change == "foreign-id" else (1, 2),
            apply=True,
            destination=tmp_path / "no.db",
        )
    assert snapshot(path) == before and not (tmp_path / "no.db").exists()


def test_fresh_running_ai_blocks_atomic_reset(client, tmp_path):
    fill(client)
    path = client.app.state.db_path
    with connect(path) as c:
        c.execute(
            "UPDATE patient_ai_runs SET status='running',heartbeat_at=? WHERE patient_id=1",
            (now(),),
        )
    before = snapshot(path)
    with pytest.raises(ValueError, match="активный"):
        reset(path, apply=True, destination=tmp_path / "active.db")
    assert snapshot(path) == before and snapshot(tmp_path / "active.db") == before


def test_unknown_foreign_dependency_rolls_back_all_deletions(client, tmp_path):
    ids = fill(client)
    path = client.app.state.db_path
    with connect(path) as c:
        c.execute(
            "CREATE TABLE outside_reference(entity_id INTEGER REFERENCES intake_entities(id))"
        )
        c.execute("INSERT INTO outside_reference VALUES(?)", (ids[1][1],))
    before = snapshot(path)
    with pytest.raises(ValueError, match="отменена"):
        reset(path, apply=True, destination=tmp_path / "dependency.db")
    assert snapshot(path) == before


def test_existing_backup_or_lost_key_never_deletes_source(client, tmp_path):
    fill(client)
    path = client.app.state.db_path
    before = snapshot(path)
    target = tmp_path / "exists.db"
    target.write_bytes(b"KEEP")
    with pytest.raises(ValueError):
        reset(path, apply=True, destination=target)
    assert target.read_bytes() == b"KEEP" and snapshot(path) == before
    from app.services.clinical_attachments import key_path
    from cryptography.fernet import Fernet

    original_key = key_path(path).read_bytes()
    key_path(path).write_bytes(Fernet.generate_key())
    with pytest.raises(ValueError, match="ключа"):
        reset(path, apply=True, destination=tmp_path / "wrong-key.db")
    assert snapshot(path) == before
    key_path(path).write_bytes(original_key)
    key_path(path).unlink()
    with pytest.raises(RuntimeError):
        reset(path, apply=True, destination=tmp_path / "missing-key.db")
    assert snapshot(path) == before


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "not-created.db"
    with pytest.raises(ValueError):
        reset(missing, apply=True)
    assert not missing.exists()


def test_concurrent_manual_edit_after_backup_is_preserved(
    client, tmp_path, monkeypatch
):
    from scripts import reset_demo_forms as module

    fill(client)
    path = client.app.state.db_path
    before = snapshot(path)
    original_backup = module.backup

    def save_then_edit(source, destination):
        original_backup(source, destination)
        with connect(path) as c:
            c.execute(
                "UPDATE answers SET comment='CONCURRENT MANUAL EDIT' WHERE assessment_id IN (SELECT id FROM assessments WHERE patient_id=1)"
            )

    monkeypatch.setattr(module, "backup", save_then_edit)
    target = tmp_path / "concurrent.db"
    with pytest.raises(ValueError, match="изменились"):
        reset(path, apply=True, destination=target)
    assert snapshot(target) == before
    after = snapshot(path)
    assert len(after["answers"]) == 3 and len(after["intake_drafts"]) == 3
    with connect(path) as c:
        assert (
            c.execute(
                "SELECT comment FROM answers WHERE assessment_id IN (SELECT id FROM assessments WHERE patient_id=1)"
            ).fetchone()[0]
            == "CONCURRENT MANUAL EDIT"
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM assessments WHERE status='completed'"
            ).fetchone()[0]
            == 3
        )
