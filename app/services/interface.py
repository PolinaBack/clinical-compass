"""Presentation preferences only; never change care plans, consents or alerts."""

import json

from fastapi import HTTPException
from app.storage import now, audit

CARDS = (
    ("wellbeing", "Общее самочувствие"),
    ("sleep", "Сон и восстановление"),
    ("treatment", "Лечение и переносимость"),
    ("scales", "Анкеты"),
    ("materials", "Материалы"),
    ("consultations", "Консультации"),
)


def read_interface(c, account_id):
    defaults = [dict(key=key, title=title, visible=True, pinned=False, position=i)
                for i, (key, title) in enumerate(CARDS, 1)]
    row = c.execute("SELECT * FROM interface_preferences WHERE account_id=?", (account_id,)).fetchone()
    if not row:
        return dict(minimal=False, revision=0, cards=defaults)
    stored = {item["key"]: item for item in json.loads(row["cards_json"])}
    for item in defaults:
        if item["key"] in stored:
            item.update(stored[item["key"]])
    return dict(minimal=bool(row["minimal"]), revision=row["revision"], cards=defaults)


def save_interface(c, user, form):
    current = read_interface(c, user["id"])
    if str(form.get("revision", "")) != str(current["revision"]):
        raise HTTPException(409, "Настройки уже изменились. Обновите страницу и повторите выбор.")

    def checked(key):
        value = form.get(key, "")
        if value not in ("", "true"):
            raise HTTPException(422, "Проверьте отметки оформления")
        return value == "true"

    minimal = checked("minimal")
    cards = []
    if user["role"] == "patient":
        for key, _ in CARDS:
            rank = str(form.get(key + "_position", ""))
            if not rank.isascii() or not rank.isdigit() or not 1 <= int(rank) <= len(CARDS):
                raise HTTPException(422, "Порядок карточек — число от 1 до 6")
            cards.append(dict(key=key, position=int(rank), visible=checked(key + "_visible"),
                              pinned=checked(key + "_pinned")))
    else:
        cards = [{k: v for k, v in item.items() if k != "title"} for item in current["cards"]]
    c.execute(
        """INSERT INTO interface_preferences(account_id,minimal,cards_json,revision,updated_at)
           VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
           minimal=excluded.minimal,cards_json=excluded.cards_json,
           revision=excluded.revision,updated_at=excluded.updated_at""",
        (user["id"], int(minimal), json.dumps(cards), current["revision"] + 1, now()),
    )
    audit(c, user["id"], "interface_preferences_saved", user["patient_id"])
