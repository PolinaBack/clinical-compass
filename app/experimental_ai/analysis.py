"""Reproducible descriptive statistics, with missingness and no diagnostic thresholds."""

import math
from statistics import mean, pstdev

from app.scales import score

VERSION = "descriptive-30d-v1"


def series_stats(rows, key):
    points = [(i, r[key]) for i, r in enumerate(rows) if r[key] is not None]
    values = [v for _, v in points]
    first = [r[key] for r in rows[:7] if r[key] is not None]
    last = [r[key] for r in rows[-7:] if r[key] is not None]
    slope = None
    if len(points) >= 3:
        mx, my = mean(i for i, _ in points), mean(values)
        variance = sum((i - mx) ** 2 for i, _ in points)
        if variance:
            slope = round(7 * sum((i - mx) * (v - my) for i, v in points) / variance, 3)
    return dict(
        n=len(values),
        missing=len(rows) - len(values),
        mean=round(mean(values), 2) if values else None,
        first_week=round(mean(first), 2) if first else None,
        last_week=round(mean(last), 2) if last else None,
        delta=round(mean(last) - mean(first), 2) if first and last else None,
        slope_per_week=slope,
        stddev=round(pstdev(values), 2) if len(values) >= 2 else None,
    )


def correlation(rows):
    pairs = [
        (r["sleep_hours"], r["wellbeing"])
        for r in rows
        if r["sleep_hours"] is not None and r["wellbeing"] is not None
    ]
    value = None
    if len(pairs) >= 7:
        mx, my = mean(x for x, _ in pairs), mean(y for _, y in pairs)
        denom = math.sqrt(
            sum((x - mx) ** 2 for x, _ in pairs) * sum((y - my) ** 2 for _, y in pairs)
        )
        if denom:
            value = round(sum((x - mx) * (y - my) for x, y in pairs) / denom, 3)
    return dict(
        n=len(pairs),
        pearson=value,
        limitation="Связь внутри одного вымышленного ряда, не причинность и не эффект лечения.",
    )


def analyze(dataset):
    rows = dataset["observations"]
    metrics = {
        key: series_stats(rows, key) for key in ("wellbeing", "sleep_hours", "anxiety")
    }
    questionnaires = []
    for item in dataset["questionnaires"]:
        answers = {
            str(i): dict(value=v, state="answered" if v is not None else "unanswered")
            for i, v in enumerate(item["values"], 1)
        }
        result = score(item["scale"], answers)
        questionnaires.append(
            dict(
                day=item["day"],
                scale=item["scale"],
                answered=result["answered"],
                count=result["count"],
                total=result["total"],
            )
        )
    facts = [
        dict(
            id="F1",
            text="Исходные жалобы вымышленного сценария: "
            + dataset["patient"]["complaint"],
        )
    ]
    for i, (key, label) in enumerate(
        [
            ("wellbeing", "Самочувствие, 0–10"),
            ("sleep_hours", "Сон, часы"),
            ("anxiety", "Субъективная тревога, 0–10"),
        ],
        2,
    ):
        facts.append(dict(id=f"F{i}", text=label, values=metrics[key]))
    facts.append(
        dict(
            id="F5",
            text="PHQ-9 и GAD-7: исследовательские суммы полных анкет, не диагноз и не доказанная клиническая динамика.",
            values=questionnaires,
        )
    )
    facts.append(
        dict(
            id="F6",
            text="Сон и самочувствие: описательная корреляция",
            values=correlation(rows),
        )
    )
    facts.append(
        dict(
            id="F7",
            text="События, придуманные автором сценария",
            values=[dict(day=r["day"], note=r["note"]) for r in rows if r["note"]],
        )
    )
    return dict(
        version=VERSION,
        days=len(rows),
        metrics=metrics,
        correlation=correlation(rows),
        questionnaires=questionnaires,
        facts=facts,
        limitations=[
            "Один полностью вымышленный сценарий, без контрольной группы и доказательства эффективности.",
            "Пропуски исключены из расчёта; ноль не подставляется.",
            "Начальная и конечная недели содержат по 7 календарных дней; наклон учитывает пропущенные дни.",
            "По дневнику нельзя установить диагноз, причинность или подобрать лечение.",
            "Русские формы шкал и их цифровые права требуют отдельной проверки; суммы здесь только технические.",
            dataset["patient"]["treatment"],
        ],
    )


def plot_segments(rows, key, maximum):
    """SVG lines split at missing observations; no interpolation over gaps."""
    segments, current = [], []
    for i, row in enumerate(rows):
        value = row[key]
        if value is None:
            if current:
                segments.append(" ".join(current))
            current = []
        else:
            current.append(f"{30 + 700 * i / 29:.1f},{145 - 120 * value / maximum:.1f}")
    if current:
        segments.append(" ".join(current))
    return segments
