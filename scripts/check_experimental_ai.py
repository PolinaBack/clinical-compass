"""One REAL Ollama call using only the protected synthetic fixture, not mock AI."""

import argparse
from app.experimental_ai import jobs, store
from app.experimental_ai.ollama import ERRORS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        # Actor 0 denotes this local research CLI, never an impersonated clinician.
        run_id = jobs.enqueue(store.default_path(), 0)
        jobs.process_one()
        with store.connect() as c:
            row = c.execute(
                "SELECT status,error_code FROM experiment_runs WHERE id=?", (run_id,)
            ).fetchone()
        print(f"Эксперимент №{run_id}: {row['status']}")
        if row["status"] != "completed":
            print("Код:", row["error_code"] or "worker_busy")
            print(
                ERRORS.get(
                    row["error_code"],
                    "Запуск уже взят другим обработчиком; проверьте его в интерфейсе.",
                )
            )
            raise SystemExit(1)
        print(
            "Реальный локальный HTTP-ответ получен и технически проверен. Это не оценка клинического качества."
        )
    except store.ExperimentError as exc:
        print("Код:", exc.code)
        print(ERRORS.get(exc.code, ERRORS["internal_error"]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
