"""Create/verify the isolated, code-owned synthetic fixture. Never seeds clinical DB."""

import argparse
from app.experimental_ai import analysis, store
from app.experimental_ai.ollama import ERRORS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        help="Separate experimental SQLite, also set COMPASS_EXPERIMENT_DB for app/worker",
    )
    args = parser.parse_args()
    path = args.db or store.default_path()
    try:
        created = store.initialize(path)
        with store.connect(path) as c:
            data = store.load(c)
        metrics = analysis.analyze(data)
    except store.ExperimentError as exc:
        print(ERRORS.get(exc.code, ERRORS["internal_error"]))
        raise SystemExit(1)
    print("Тестовый режим · только синтетические данные")
    print("База:", store.protected_path(path))
    print(
        "Создана отдельная база."
        if created
        else "Встроенный сценарий проверен; существующие данные и история сохранены."
    )
    print("Мария Соколова: один полностью вымышленный пациент, 30 дней, 6 анкет.")
    print("Программный анализ готов; генерация не запускалась. Модели не скачивались.")
    print(
        "Самочувствие: первая/последняя недели",
        metrics["metrics"]["wellbeing"]["first_week"],
        metrics["metrics"]["wellbeing"]["last_week"],
    )


if __name__ == "__main__":
    main()
