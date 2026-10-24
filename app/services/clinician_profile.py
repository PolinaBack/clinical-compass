"""Compact, source-labelled view of submitted anamnesis; no invented clinical facts."""

from app.services.clinical_forms import display


def build(context):
    data = context["intake_data"]
    answers = data.get("answers", {})
    entities = data.get("entities", [])
    submission = context.get("intake_submission")

    def text(section, key):
        value = answers.get(section, {}).get(key, {})
        return display(value) if value else None

    def descriptions(kind):
        return [
            dict(
                id=e["id"],
                version=e["version"],
                name=display(
                    e["data"].get("specialty" if kind == "visit" else "name", {})
                ),
                data=e["data"],
            )
            for e in entities
            if e["kind"] == kind
        ]

    diseases, medicines, reactions, visits = (
        descriptions(k) for k in ("condition", "medication", "reaction", "visit")
    )
    # An old drug is never silently labelled as being currently taken.
    current = [
        m
        for m in medicines
        if m["data"].get("category", {}).get("value")
        not in ("Принимал(а) раньше", "Недавно отменено")
    ]
    medicines_unknown = [
        m for m in current if m["data"].get("category", {}).get("status") != "answered"
    ]
    prefs = context["intake_preferences"]
    calculations = context["intake_calculations"]
    measurements = (
        "Скрыто в настройках анкеты"
        if prefs.get("hide_weight")
        else " · ".join(
            v
            for v in [
                (
                    str(text("measurements", "height")) + " см"
                    if answers.get("measurements", {}).get("height", {}).get("status")
                    == "answered"
                    else None
                ),
                (
                    str(text("measurements", "weight")) + " кг"
                    if answers.get("measurements", {}).get("weight", {}).get("status")
                    == "answered"
                    else None
                ),
                (
                    "ИМТ " + str(calculations["bmi"])
                    if calculations["bmi"] is not None
                    else None
                ),
            ]
            if v
        )
        or "Не указаны"
    )
    age = (
        str(calculations["age"])
        if calculations["age"] is not None
        else "Возраст не указан"
    )
    summary = [
        ("Возраст и пол", age + " · " + (text("personal", "sex") or "Пол не указан")),
        ("Рост / масса / ИМТ", measurements),
        (
            "Учёба / работа",
            text("occupation", "work_role")
            or text("occupation", "study_area")
            or text("occupation", "status")
            or "Не указано",
        ),
        (
            "Основные жалобы",
            text("complaints", "groups")
            or text("complaints", "top_three")
            or "Не указаны",
        ),
        (
            "Заболевания",
            " · ".join(d["name"] for d in diseases)
            or text("health", "description")
            or "Не указаны",
        ),
        (
            "Лекарства: текущие и статус не уточнён",
            " · ".join(m["name"] for m in current)
            or text("medicines", "current")
            or "Не указаны",
        ),
        (
            "Аллергии и реакции",
            " · ".join(r["name"] for r in reactions)
            or text("medicines", "reactions")
            or "Не указаны",
        ),
        (
            "Последний специалист",
            (
                visits[-1]["name"]
                if visits
                else text("visits", "specialties") or "Не указан"
            ),
        ),
    ]
    checks = []
    for item in diseases + medicines + reactions:
        review = context.get("intake_reviews", {}).get(
            f"entity:{item['id']}:{item['version']}", {}
        )
        if review.get("status") != "confirmed":
            checks.append(item["name"])
    for item in context.get("intake_deferred", []):
        if not item["category"].get("sensitive"):
            checks.append(item["field"]["label"] + " · " + item["text"])
    return dict(
        summary_rows=summary,
        diseases=diseases,
        medicines=current,
        all_medicines=medicines,
        reactions=reactions,
        visits=visits,
        medicines_unknown=bool(medicines_unknown),
        verification=list(dict.fromkeys(checks)),
        completion=context["intake_overall"],
        files_count=len(data.get("attachments", [])),
        source_id=submission["id"] if submission else None,
        age=age,
    )
