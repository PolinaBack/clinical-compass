"""Reusable diary templates; assigning copies a definition into the versioned diary."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from fastapi import HTTPException
from app.storage import audit, now
from app.services.clinical_forms import today


@lru_cache(maxsize=1)
def catalog():
    return json.loads(
        (Path(__file__).parents[1] / "references/diary_cards.json").read_text(
            encoding="utf-8"
        )
    )


CATEGORIES = {c["key"]: c for c in catalog()["categories"]}
FREQUENCIES = {
    "daily": "Ежедневно",
    "three_week": "3 раза в неделю · пн, ср, пт",
    "weekly": "Раз в неделю",
    "event": "По событию",
    "as_needed": "По потребности",
    "agreed": "По согласованию",
    "optional": "По желанию",
    "after_task": "После задания",
    "intake_days": "В дни приёма",
    "template": "По настройкам карточки",
}
DEFAULT_FREQUENCIES = {
    "Ежедневно": "daily",
    "2–4 раза в неделю": "three_week",
    "2–3 раза в неделю": "three_week",
    "Раз в неделю": "weekly",
    "По событию": "event",
    "По потребности": "as_needed",
    "По согласованию": "agreed",
    "По желанию": "optional",
    "После задания": "after_task",
    "В дни приёма": "intake_days",
    "Если настроено": "agreed",
}


def palette(key):
    return CATEGORIES.get(key, CATEGORIES["specialized"])


def apply_frequency(definition, frequency):
    if frequency not in FREQUENCIES:
        raise HTTPException(422, "Выберите частоту заполнения")
    if frequency == "template":
        return
    definition.update(weekdays=list(range(1, 8)), interval=1, start="", end="")
    if frequency == "three_week":
        definition.update(frequency="weekdays", weekdays=[1, 3, 5])
    elif frequency == "weekly":
        definition.update(frequency="interval", interval=7, start=today().isoformat())
    else:
        definition["frequency"] = frequency


def schedule_label(definition):
    frequency = definition.get("frequency", "daily")
    if frequency == "weekdays":
        names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        return "По дням недели: " + ", ".join(
            names[i - 1] for i in definition["weekdays"]
        )
    if frequency == "interval":
        return f"Каждые {definition['interval']} дн. с {definition['start']}"
    return FREQUENCIES.get(frequency, frequency)


def builtin_cards():
    result = []
    for raw in catalog()["cards"]:
        card = deepcopy(raw)
        card.update(
            template_key="ready-" + card["key"],
            custom=False,
            prompt=card["prompt"],
            palette=card["category"],
            hint=card.get("hint", card["description"]),
            recall_period="За выбранный день",
            time_window="",
            required=False,
            trigger="always",
            threshold=None,
            position=0,
            advanced=True,
            default_frequency=DEFAULT_FREQUENCIES[card["recommended_frequency"]],
        )
        if card["kind"] == "effects":
            card["effect_groups"] = deepcopy(catalog()["effects"])
        apply_frequency(card, card["default_frequency"])
        result.append(card)
    return result


def cards(c, user):
    result = builtin_cards()
    for row in c.execute(
        "SELECT * FROM diary_card_templates WHERE owner_id=? ORDER BY id", (user["id"],)
    ):
        result.append(
            dict(
                json.loads(row["definition"]),
                template_key=f"custom-{row['id']}",
                custom=True,
                default_frequency="template",
            )
        )
    return result


def save_template(c, user, form):
    from app.services.diary_questions import configuration

    if user["role"] != "doctor":
        raise HTTPException(403, "Карточки создаёт специалист")
    token = str(form.get("client_token", ""))
    if (
        not 16 <= len(token) <= 100
        or not token.isascii()
        or not token.replace("-", "").replace("_", "").isalnum()
    ):
        raise HTTPException(422, "Обновите форму создания карточки")
    previous = c.execute(
        "SELECT id FROM diary_card_templates WHERE owner_id=? AND client_token=?",
        (user["id"], token),
    ).fetchone()
    if previous:
        return previous["id"]
    definition = configuration(form)
    ident = c.execute(
        "INSERT INTO diary_card_templates(owner_id,definition,client_token,created_at) VALUES(?,?,?,?)",
        (user["id"], json.dumps(definition, ensure_ascii=False), token, now()),
    ).lastrowid
    audit(c, user["id"], "diary_card_template_created", None, ident)
    return ident


def assign(c, user, pid, key, frequency):
    from app.services.intake import check_scope
    from app.services import diary_questions as questions

    check_scope(c, user, pid, doctor=True)
    card = next((t for t in cards(c, user) if t["template_key"] == key), None)
    if not card:
        raise HTTPException(404, "Карточка не найдена")
    existing = next(
        (
            q
            for q in questions.definitions(c, pid, True)
            if q.get("template_key") == key
        ),
        None,
    )
    if existing:
        if not existing["active"]:
            version = existing["version"] + 1
            definition = {
                k: v
                for k, v in existing.items()
                if k
                not in {
                    "id",
                    "patient_id",
                    "created_by",
                    "created_at",
                    "version",
                    "active",
                }
            }
            c.execute(
                "INSERT INTO diary_question_versions VALUES(?,?,?,?,?)",
                (
                    existing["id"],
                    version,
                    json.dumps(definition, ensure_ascii=False),
                    user["id"],
                    now(),
                ),
            )
            c.execute(
                "UPDATE diary_question_config SET version=?,active=1 WHERE metric_id=?",
                (version, existing["id"]),
            )
            audit(c, user["id"], "diary_question_activated", pid, existing["id"])
        return existing["id"]
    definition = {
        k: v
        for k, v in card.items()
        if k not in {"key", "custom", "default_frequency", "code", "answer_type"}
    }
    apply_frequency(definition, frequency)
    return questions.save_config(c, pid, user, definition)
