"""A 90-day visual journey based on distinct saved diary dates, never symptoms."""

from datetime import date
from app.services.clinical_forms import today

GOAL = 90
TITLES = {
    "spring": "Весенний путь",
    "summer": "Летний путь",
    "autumn": "Осенний путь",
    "winter": "Зимний путь",
}


def season_for(day):
    if 3 <= day.month <= 5:
        return "spring"
    if 6 <= day.month <= 8:
        return "summer"
    if 9 <= day.month <= 11:
        return "autumn"
    return "winter"


def progress(c, pid, reference=None):
    reference = reference or today()
    unique_days = 0
    for row in c.execute("SELECT DISTINCT day FROM diary WHERE patient_id=?", (pid,)):
        try:
            parsed = date.fromisoformat(row["day"])
        except (TypeError, ValueError):
            continue
        if parsed.isoformat() == row["day"] and parsed <= reference:
            unique_days += 1
    days = min(unique_days, GOAL)
    season = season_for(reference)
    return dict(
        unique_days=unique_days,
        days=days,
        goal=GOAL,
        percent=days / GOAL * 100,
        complete=days == GOAL,
        season=season,
        season_title=TITLES[season],
        value_text=f"Заполнено {days} из {GOAL} дней дневника",
        as_of=reference.isoformat(),
    )
