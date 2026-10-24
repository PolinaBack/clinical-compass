"""Local durable worker. Does not open clinical records or download models."""

import argparse
import time
from app.experimental_ai import jobs, store
from app.experimental_ai.ollama import ERRORS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued experiment"
    )
    args = parser.parse_args()
    print(
        "Обработчик эксперимента: только встроенные синтетические данные. Ctrl+C — остановить.",
        flush=True,
    )
    try:
        while True:
            worked = jobs.process_one()
            if worked:
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
