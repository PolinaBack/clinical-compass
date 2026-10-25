"""Exact registry lookups, never probabilities, diagnoses or treatment ranking."""

from app.adverse_catalog import ELIGIBLE
from app.services import drug_library as library


def records(c):
    drugs = {d["code"]: d for d in library.search(c)}
    rows = []
    for key, value in library.current(c, "adverse").items():
        raw = value["raw"]
        code = str(raw["ID препарата"])
        if code not in drugs:
            continue
        drug = drugs[code]
        rows.append(
            dict(
                key=key,
                drug=code,
                name=drug["name"],
                slug=drug["slug"],
                system=raw["Категория"],
                medical=raw["Медицинский термин"],
                label=raw.get("Для пациента — текст") or raw["Медицинский термин"],
                frequency=raw.get("Частота по источнику")
                or "частота требует уточнения",
                row=value["row"],
                import_id=value["_import"],
                validation=raw.get("Примечание к валидации") or "Требует сверки",
            )
        )
    return sorted(rows, key=lambda r: (r["system"], r["row"]))


def filtered(rows, drug="", system="", q=""):
    words = library.normalize(q).split()
    return [
        r
        for r in rows
        if (not drug or r["drug"] == drug)
        and (not system or r["system"] == system)
        and all(w in library.normalize(r["label"] + " " + r["medical"]) for w in words)
    ]


def preference_snapshot(c, pid, path=None):
    from app.services.intake import read_submission

    from app.services.medication_preferences import sent

    path = path or c.execute("PRAGMA database_list").fetchone()["file"]
    preference = sent(c, path, pid)
    submission = read_submission(c, pid)

    # A private draft never participates. Explicitly sent intake and medication
    # preferences share a timestamp/audit ordering, so the last sent response wins.
    def sequence(action, ident):
        return c.execute(
            "SELECT coalesce(max(id),0) FROM audit WHERE patient_id=? AND action=? AND object_id=?",
            (pid, action, ident),
        ).fetchone()[0]

    if preference and (
        not submission
        or (
            preference["created_at"],
            sequence("medication_preferences_sent", preference["revision"]),
        )
        >= (submission["created_at"], sequence("intake_submitted", submission["id"]))
    ):
        return dict(
            submission_id=None,
            source="medication_preferences",
            preference_revision=preference["revision"],
            selected=preference["selected"],
            primary=preference["primary"],
            comment=preference["comment"],
            submitted_at=preference["created_at"],
        )
    answers = (
        submission["data"].get("answers", {}).get("medicines", {}) if submission else {}
    )
    selected = answers.get("avoid_reactions", {})
    codes = selected.get("value", []) if selected.get("status") == "answered" else []
    codes = [v for v in codes if v in ELIGIBLE][:3] if isinstance(codes, list) else []
    primary = answers.get("primary_reaction", {})
    primary = primary.get("value") if primary.get("status") == "answered" else None
    return dict(
        submission_id=submission["id"] if submission else None,
        selected=codes,
        primary=primary if primary in codes else None,
    )


def preference_choices(snapshot):
    return [
        {**ELIGIBLE[code], "primary": code == snapshot.get("primary")}
        for code in snapshot.get("selected", [])
        if code in ELIGIBLE
    ]


def matches(rows, drug, snapshot):
    result = []
    for choice in preference_choices(snapshot):
        for row in rows:
            # No fuzzy matching, no merging of similar terms or organ systems.
            if (
                row["drug"] == drug
                and row["system"] == choice["system"]
                and row["medical"].casefold() == choice["medical"].casefold()
            ):
                result.append(
                    {
                        **choice,
                        "frequency": row["frequency"],
                        "registry_key": row["key"],
                        "registry_import": row["import_id"],
                    }
                )
    return result


def preference_check(rows, drug, snapshot):
    """Describe exact catalogue matches, including unknown and unselected states."""
    choices = preference_choices(snapshot)
    found = matches(rows, drug, snapshot)
    drug_rows = [row for row in rows if row["drug"] == drug]
    has_data = bool(drug_rows)
    effects = []
    for choice in choices:
        evidence = [row for row in found if row["code"] == choice["code"]]
        other_systems = sorted(
            {
                row["system"]
                for row in drug_rows
                if row["medical"].casefold() == choice["medical"].casefold()
                and row["system"] != choice["system"]
            }
        )
        effects.append(
            dict(
                **choice,
                state=(
                    "listed"
                    if evidence
                    else (
                        "review"
                        if other_systems
                        else "not_found" if has_data else "unknown"
                    )
                ),
                other_systems=other_systems,
            )
        )
    count = sum(effect["state"] == "listed" for effect in effects)
    review_count = sum(effect["state"] == "review" for effect in effects)
    return dict(
        state=(
            "not_selected"
            if not choices
            else (
                "no_data"
                if not has_data
                else "matches" if count else "review" if review_count else "not_found"
            )
        ),
        effects=effects,
        matched_count=count,
        review_count=review_count,
        total=len(effects),
    )


def record_count(n):
    return f"{n} " + (
        "записей"
        if 11 <= n % 100 <= 14
        else "запись" if n % 10 == 1 else "записи" if 2 <= n % 10 <= 4 else "записей"
    )
