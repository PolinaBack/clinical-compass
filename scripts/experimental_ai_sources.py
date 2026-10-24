"""Fetch fixed public sources and separately record a human version review."""

import argparse
import httpx
from app.experimental_ai import sources, store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--fetch", choices=list(sources.CATALOG))
    action.add_argument("--approve", choices=list(sources.CATALOG))
    parser.add_argument("--sha256")
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    try:
        if args.fetch:
            with httpx.Client(
                timeout=30, follow_redirects=False, trust_env=False
            ) as client:
                meta = sources.fetch(args.fetch, client)
            print("Документ получен, но ещё НЕ подтверждён человеком.")
            print("Файл:", sources.folder() / (args.fetch + ".html"))
            print("SHA-256:", meta["sha256"])
            print(
                "Проверьте издателя, заголовок, версию и содержимое; затем используйте --approve с этим отпечатком и --reviewer."
            )
        elif args.approve:
            if not args.sha256 or not args.reviewer:
                parser.error(
                    "Для подтверждения нужны --sha256 и --reviewer; подтвердите только лично проверенный документ."
                )
            sources.approve(args.approve, args.sha256, args.reviewer)
            print(
                "Проверка конкретной версии сохранена. Это не подтверждение клинической применимости или эффективности."
            )
        else:
            for item in sources.catalog():
                print(
                    item["id"],
                    "проверен" if item["verified"] else "НЕ проверен",
                    item["checked_at"] or "даты нет",
                )
    except store.ExperimentError:
        print(
            "Документ недоступен или проверка версии не пройдена. Подтверждение не создано."
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
