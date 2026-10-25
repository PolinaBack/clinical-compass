"""Local durable worker. Does not open clinical records or download models."""

import argparse
import time
from app.experimental_ai import jobs, store
from app.experimental_ai.ollama import ERRORS
from app.services.patient_ai import process_one as process_card
from app.services.aviandr_jobs import process_one as process_aviandr
from app.storage import default_path as clinical_path
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued experiment"
    )
    args = parser.parse_args()
    print(
        "Локальный ИИ-обработчик: карты пациентов и отдельная демолаборатория. Содержимое не выводится. Ctrl+C — остановить.",
        flush=True,
    )
    try:
        while True:
            card_worked = process_card() if Path(clinical_path()).is_file() else False
            av_worked = process_aviandr() if Path(clinical_path()).is_file() else False
            try:
                demo_worked = jobs.process_one() if not card_worked else False
            except store.ExperimentError as exc:
                if exc.code == "demo_not_initialized":
                    demo_worked = False
                else:
                    raise
            worked = card_worked or av_worked or demo_worked
            if av_worked:
                print(
                    "Aviandr explanation job processed; результат в карте.", flush=True
                )
            if card_worked:
                print(
                    "Card AI job processed; подробности доступны в карте, содержимое в журнал не выводится.",
                    flush=True,
                )
            if demo_worked:
                with store.connect() as c:
                    row = c.execute(
                        "SELECT id,status,error_code FROM experiment_runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    print(
                        f"Experiment run={row['id']} status={row['status']} code={row['error_code'] or '-'}; содержимое ответа в журнал не выводится.",
                        flush=True,
                    )
            if args.once:
                break
            if not worked:
                time.sleep(2)
    except KeyboardInterrupt:
        print(
            "Обработчик остановлен. Незавершённый запуск можно повторить после истечения аренды."
        )
    except store.ExperimentError as exc:
        print(ERRORS.get(exc.code, ERRORS["internal_error"]), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
