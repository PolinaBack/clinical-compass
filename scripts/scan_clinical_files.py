"""Explicit local ClamAV retry. Logs IDs/status only, never filenames/content/keys."""

import argparse
import shutil
from pathlib import Path
from app.storage import default_path, connect
from app.services import clinical_attachments as files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    engine = shutil.which("clamdscan") or shutil.which("clamscan")
    if not engine:
        print("ClamAV не найден. Проверка не выполнена; файлы не помечены безопасными.")
        return 1
    if args.check:
        print("Локальный ClamAV найден. Файлы и база не читались.")
        return 0
    if not 1 <= args.limit <= 200:
        parser.error("--limit: от 1 до 200")
    path = default_path()
    if not path.is_file():
        parser.error("Существующая база не найдена; сначала запустите приложение")
    with connect(path) as c:
        rows = c.execute(
            "SELECT * FROM clinical_attachments WHERE deleted_at IS NULL AND scan_status IN ('not_scanned','error') ORDER BY id LIMIT ?",
            (args.limit,),
        ).fetchall()
    for row in rows:
        with connect(path) as c:
            content = files.decrypt(c, path, row)
        state, name = files.scan(content, Path(row["filename"]).suffix)
        with connect(path) as c:
            c.execute(
                "UPDATE clinical_attachments SET scan_status=?,scan_engine=? WHERE id=? AND deleted_at IS NULL AND sha256=?",
                (state, name, row["id"], row["sha256"]),
            )
        print("Файл #" + str(row["id"]) + " · " + state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
