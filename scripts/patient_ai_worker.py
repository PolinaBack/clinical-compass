"""Process real, authorized card snapshots using pretrained LOCAL Ollama."""

import argparse
import time
from pathlib import Path
from app.storage import default_path, connect
from app.services.patient_ai import process_one
from app.services.aviandr_jobs import process_one as process_aviandr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    print(
        "Обработчик ИИ-карт запущен. Только локальная Ollama; содержимое карт не выводится. Ctrl+C — остановить.",
        flush=True,
    )
    try:
        while True:
            worked = process_one() if Path(default_path()).is_file() else False
            if worked:
                with connect(default_path()) as c:
                    row = c.execute(
                        "SELECT id,status,error_code FROM patient_ai_runs WHERE id=?",
                        (worked,),
                    ).fetchone()
                    print(
                        f"Card AI job={row['id']} status={row['status']} code={row['error_code'] or '-'}",
                        flush=True,
                    )
            av_worked = process_aviandr() if Path(default_path()).is_file() else False
            if av_worked:
                print(
                    "Aviandr explanation job processed; результат и ошибки доступны в карте.",
                    flush=True,
                )
            if args.once:
                break
            if not worked and not av_worked:
                time.sleep(2)
    except KeyboardInterrupt:
        print("Обработчик остановлен.")


if __name__ == "__main__":
    main()
