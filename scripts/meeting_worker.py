"""Local persistent queue. Start separately from uvicorn; no implicit model download."""

import argparse
import time
from app.storage import default_path
from app.services.meetings import process_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued task and exit"
    )
    args = parser.parse_args()
    print(
        "Обработчик локальных встреч запущен. Ctrl+C — остановить. Содержимое записей в журнал не выводится."
    )
    try:
        while True:
            worked = process_one(default_path())
            if args.once:
                break
            if not worked:
                time.sleep(2)
    except KeyboardInterrupt:
        print(
            "Обработчик остановлен. Незавершённые задачи восстановятся по сроку аренды; повтор запускает врач."
        )


if __name__ == "__main__":
    main()
