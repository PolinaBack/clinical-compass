"""Consistent SQLite backup; run from the project root. No third-party packages."""

import argparse
from contextlib import closing
import os
import sqlite3
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ValueError("Source database does not exist")
    key_destination = Path(str(destination) + ".key")
    if destination.exists() or key_destination.exists():
        raise ValueError("Destination already exists; choose a new file name")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    ) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
            attachments = dst.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clinical_attachments'"
            ).fetchone()
            patient_ai = dst.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='patient_ai_runs'"
            ).fetchone()
            medicine_drafts = dst.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='medicine_drafts'"
            ).fetchone()
            care_orders = dst.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='care_orders'"
            ).fetchone()
            medication_preferences = dst.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='medication_preference_versions'"
            ).fetchone()
            research_data = any(
                dst.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                and dst.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("aviandr_observations", "aviandr_runs")
            )
            if (
                research_data
                or (
                    patient_ai
                    and dst.execute("SELECT 1 FROM patient_ai_runs LIMIT 1").fetchone()
                )
                or (
                    medicine_drafts
                    and dst.execute("SELECT 1 FROM medicine_drafts LIMIT 1").fetchone()
                )
                or (
                    care_orders
                    and dst.execute("SELECT 1 FROM care_orders LIMIT 1").fetchone()
                )
                or (
                    medication_preferences
                    and dst.execute(
                        "SELECT 1 FROM medication_preference_versions LIMIT 1"
                    ).fetchone()
                )
                or attachments
                and dst.execute(
                    "SELECT 1 FROM clinical_attachments WHERE content IS NOT NULL LIMIT 1"
                ).fetchone()
            ):
                sidecar = Path(str(source) + ".key")
                key_source = Path(
                    os.getenv(
                        "COMPASS_ATTACHMENT_KEY_FILE",
                        str(
                            sidecar
                            if sidecar.exists()
                            else source.parent / "attachments.key"
                        ),
                    )
                )
                if not key_source.is_file():
                    raise RuntimeError(
                        "Encrypted attachments exist but their key is missing; this database backup alone cannot restore them"
                    )
                with os.fdopen(
                    os.open(
                        key_destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                    ),
                    "wb",
                ) as key:
                    key.write(key_source.read_bytes())
            os.chmod(destination, 0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path("data/compass.db"))
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("Backup created and verified:", args.destination)
    if Path(str(args.destination) + ".key").exists():
        print(
            "Ключ вложений сохранён рядом с копией (.db.key). Для восстановления нужны оба файла; содержимое ключа не выводится."
        )
