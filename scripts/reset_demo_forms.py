"""Preview or reset questionnaires and About me for the two known demo profiles.

No startup hook and no web endpoint. Requires explicit --apply, makes a complete
backup first, refuses changed identity/active inference, then commits atomically.
"""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from app.storage import default_path
from scripts.backup import backup

DEMOS = {
    1: ("Демо-пациент А", {"patient-a", "patient1"}),
    2: ("Демо-пациент Б", {"patient-b", "patient2"}),
}


def database(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("База не найдена. Проверьте путь; новая база не создаётся.")
    c = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def validate_targets(c, pids):
    if not pids or len(set(pids)) != len(pids) or not set(pids) <= DEMOS.keys():
        raise ValueError("Разрешены только исходные демо-профили 1 и 2, без повторов.")
    for pid in pids:
        name, usernames = DEMOS[pid]
        patient = c.execute("SELECT name FROM patients WHERE id=?", (pid,)).fetchone()
        users = c.execute(
            "SELECT username,role FROM accounts WHERE patient_id=?", (pid,)
        ).fetchall()
        if (
            not patient
            or patient[0] != name
            or any(r[0] not in usernames or r[1] != "patient" for r in users)
        ):
            raise ValueError(
                f"Профиль {pid} не соответствует исходному демо-профилю. Очистка заблокирована."
            )


def queries(c, pids):
    validate_targets(c, pids)
    marks = ",".join("?" for _ in pids)
    aids = f"SELECT id FROM assessments WHERE patient_id IN ({marks})"
    answer_ids = f"SELECT id FROM answers WHERE assessment_id IN ({aids})"
    result = {
        "assessments": (
            f"SELECT * FROM assessments WHERE patient_id IN ({marks}) ORDER BY id",
            pids,
        ),
        "answers": (
            f"SELECT * FROM answers WHERE assessment_id IN ({aids}) ORDER BY id",
            pids,
        ),
        "answer_history": (
            f"SELECT * FROM answer_history WHERE answer_id IN ({answer_ids}) ORDER BY id",
            pids,
        ),
        "intake_reviews": (
            f"SELECT * FROM intake_reviews WHERE submission_id IN (SELECT id FROM intake_submissions WHERE patient_id IN ({marks})) ORDER BY id",
            pids,
        ),
        "clinical_attachments": (
            f"SELECT * FROM clinical_attachments WHERE patient_id IN ({marks}) AND scope='intake' ORDER BY id",
            pids,
        ),
    }
    for table in (
        "assessment_events",
        "audio_notes",
        "video_recordings",
        "video_choices",
    ):
        # Invalid cross-patient links are not interpreted as permission to delete.
        if c.execute(
            f"SELECT 1 FROM {table} WHERE assessment_id IN ({aids}) AND patient_id NOT IN ({marks}) LIMIT 1",
            (*pids, *pids),
        ).fetchone():
            raise ValueError(
                "Обнаружена несогласованная связь с другой картой. Очистка заблокирована."
            )
        result[table] = (
            f"SELECT * FROM {table} WHERE assessment_id IN ({aids}) ORDER BY assessment_id",
            pids,
        )
    for table in (
        "intake_entity_links",
        "intake_entities",
        "intake_changes",
        "intake_submissions",
        "intake_drafts",
        "patient_profiles",
        "screenings",
    ):
        result[table] = (
            f"SELECT * FROM {table} WHERE patient_id IN ({marks}) ORDER BY rowid",
            pids,
        )
    if c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='patient_ai_runs'"
    ).fetchone():
        result["patient_ai_runs"] = (
            f"SELECT * FROM patient_ai_runs WHERE patient_id IN ({marks}) ORDER BY id",
            pids,
        )
    return result


def request_tokens(c, pids):
    aids = {
        r[0]
        for r in c.execute(
            "SELECT id FROM assessments WHERE patient_id IN ("
            + ",".join("?" for _ in pids)
            + ")",
            pids,
        )
    }
    result = []
    for row in c.execute("SELECT * FROM save_requests ORDER BY token"):
        try:
            payload = json.loads(row["payload"])
            if (
                isinstance(payload, dict)
                and type(payload.get("assessment")) is int
                and payload["assessment"] in aids
            ):
                result.append(tuple(row))
        except (ValueError, TypeError):
            pass
    return result


def state(c, pids):
    counts, checksum = {}, hashlib.sha256()
    for table, (query, parameters) in queries(c, pids).items():
        count = 0
        for row in c.execute(query, parameters):
            # Hash blobs without ever serialising/logging their private contents.
            values = [
                (
                    dict(blob_sha256=hashlib.sha256(v).hexdigest())
                    if isinstance(v, bytes)
                    else v
                )
                for v in row
            ]
            checksum.update(json.dumps([table, values], ensure_ascii=False).encode())
            if (
                table != "assessments"
                or row["status"] != "draft"
                or row["completed_at"]
                or row["seen_at"]
            ):
                count += 1
        counts[table] = count
    tokens = request_tokens(c, pids)
    counts["save_requests"] = len(tokens)
    checksum.update(json.dumps(tokens, ensure_ascii=False).encode())
    return counts, checksum.hexdigest()


def verify_backup_key(c, destination, pids):
    """Verify recoverability of encrypted records that the reset will remove."""
    from cryptography.fernet import Fernet, InvalidToken

    statements = queries(c, pids)
    key = None
    try:
        for table, columns in [
            ("clinical_attachments", ("content",)),
            ("patient_ai_runs", ("snapshot", "result")),
        ]:
            if table not in statements:
                continue
            query, parameters = statements[table]
            for row in c.execute(query, parameters):
                for column in columns:
                    value = row[column]
                    if value is None:
                        continue
                    if key is None:
                        key = Fernet(Path(str(destination) + ".key").read_bytes())
                    key.decrypt(value)
    except (OSError, ValueError, TypeError, InvalidToken):
        raise ValueError(
            "Копия создана, но проверка ключа восстанавливаемых данных не прошла. Очистка не выполнена."
        ) from None


def reset(path=None, *, pids=(1, 2), apply=False, destination=None):
    path = Path(path or default_path()).resolve()
    pids = tuple(pids)
    with closing(database(path)) as c:
        counts, checksum = state(c, pids)
    result = dict(
        patient_ids=list(pids),
        scope="questionnaires-and-about-me",
        records=counts,
        changed=False,
        backup=None,
    )
    if not apply or not any(counts.values()):
        return result
    target = (
        Path(destination)
        if destination
        else path.parent
        / "backups"
        / (
            "demo-forms-"
            + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ-")
            + uuid.uuid4().hex[:8]
            + ".db"
        )
    )
    backup(path, target)
    with closing(database(target)) as saved:
        if state(saved, pids)[1] != checksum:
            raise ValueError(
                "Данные изменились во время резервного копирования. Очистка не выполнена."
            )
        verify_backup_key(saved, target, pids)
    connection = database(path)
    try:
        with connection as c:
            c.execute("BEGIN IMMEDIATE")
            if state(c, pids)[1] != checksum:
                raise ValueError(
                    "Данные изменились после резервного копирования. Очистка не выполнена; остановите сервер/обработчики и повторите."
                )
            marks = ",".join("?" for _ in pids)
            if "patient_ai_runs" in counts:
                recent = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(
                    timespec="seconds"
                )
                if c.execute(
                    f"SELECT 1 FROM patient_ai_runs WHERE patient_id IN ({marks}) AND status='running' AND (heartbeat_at IS NULL OR heartbeat_at>=?)",
                    (*pids, recent),
                ).fetchone():
                    raise ValueError(
                        "Есть активный ИИ-анализ. Остановите обработчик и подождите 90 секунд; очистка не выполнена."
                    )
            for token, _, _ in request_tokens(c, pids):
                c.execute("DELETE FROM save_requests WHERE token=?", (token,))
            # Child records first, before resetting form headers or deleting entities.
            order = [
                "answer_history",
                "answers",
                "assessment_events",
                "audio_notes",
                "video_recordings",
                "video_choices",
                "intake_reviews",
                "clinical_attachments",
                "intake_entity_links",
                "intake_changes",
                "intake_submissions",
                "intake_drafts",
                "intake_entities",
                "patient_profiles",
                "screenings",
                "patient_ai_runs",
            ]
            statements = queries(c, pids)
            for table in order:
                if table not in statements:
                    continue
                query, parameters = statements[table]
                where = query.split(" WHERE ", 1)[1].rsplit(" ORDER BY ", 1)[0]
                c.execute(f"DELETE FROM {table} WHERE {where}", parameters)
            c.execute(
                f"UPDATE assessments SET status='draft',completed_at=NULL,seen_at=NULL,revision=revision+1 WHERE patient_id IN ({marks})",
                pids,
            )
            if c.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Проверка связей не пройдена. Очистка отменена.")
    except sqlite3.IntegrityError:
        raise ValueError(
            "Есть дополнительные связи с сохраняемыми данными. Очистка полностью отменена; резервная копия сохранена."
        ) from None
    finally:
        connection.close()
    result.update(changed=True, backup=str(target))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=default_path())
    parser.add_argument(
        "--patients", type=int, nargs="+", choices=[1, 2], default=[1, 2]
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually reset both sets of forms after a complete backup",
    )
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        report = reset(
            args.database, pids=args.patients, apply=args.apply, destination=args.backup
        )
    except (ValueError, RuntimeError, sqlite3.DatabaseError, OSError) as exc:
        # Fixed validation/SQLite messages only; never dump record contents.
        print(
            "Очистка не выполнена:",
            (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "Проверьте доступность, формат и связи базы."
            ),
        )
        raise SystemExit(1)
    print("Демо-профили:", ", ".join(map(str, report["patient_ids"])))
    print(
        "Область: ответы/история/медиа опросников, первичный анамнез/файлы/прежние поля «Обо мне», устаревшие ИИ-снимки."
    )
    for table, count in report["records"].items():
        if count:
            print(f"{table}: {count}")
    if report["changed"]:
        print("Сброс выполнен. Полная резервная копия:", report["backup"])
    elif not any(report["records"].values()):
        print("Эти формы уже пустые. Изменений нет.")
    else:
        print(
            "Это только предварительный просмотр. Для сброса выполните ту же команду с --apply."
        )
    print(
        "Аккаунты, согласия/настройки, дневник, сообщения, семейное дерево, консультации и другие пациенты сохранены."
    )


if __name__ == "__main__":
    main()
