"""Versioned, entirely invented fixtures. New scenarios must be reviewed code changes."""

from datetime import date, timedelta
import hashlib
import json

SCENARIO = "maria-sokolova-30d-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def maria():
    wellbeing = [
        3,
        4,
        3,
        4,
        4,
        3,
        4,
        None,
        4,
        5,
        4,
        5,
        4,
        5,
        5,
        5,
        6,
        5,
        None,
        6,
        6,
        5,
        6,
        3,
        6,
        7,
        6,
        7,
        6,
        7,
    ]
    sleep = [
        5,
        5.5,
        5,
        6,
        5.5,
        5,
        6,
        5.5,
        6,
        None,
        6,
        6.5,
        6,
        6.5,
        6.5,
        6,
        7,
        6.5,
        6,
        7,
        7,
        6.5,
        7,
        4.5,
        7,
        7.5,
        7,
        7.5,
        7,
        7.5,
    ]
    anxiety = [
        8,
        7,
        8,
        7,
        7,
        8,
        7,
        7,
        7,
        6,
        7,
        6,
        6,
        6,
        6,
        6,
        5,
        6,
        6,
        5,
        5,
        6,
        5,
        8,
        5,
        4,
        5,
        4,
        5,
        4,
    ]
    events = {
        1: "Трудно засыпать, меньше сил и интереса к прогулкам.",
        15: "Начала записывать время сна и выходить на короткие прогулки.",
        24: "Вымышленный напряжённый рабочий день, легла поздно.",
        30: "Чаще удаётся отдыхать, хочется обсудить сохраняющееся беспокойство.",
    }
    observations = [
        dict(
            day=(date(2026, 9, 1) + timedelta(days=i)).isoformat(),
            wellbeing=wellbeing[i],
            sleep_hours=sleep[i],
            anxiety=anxiety[i],
            note=events.get(i + 1, ""),
        )
        for i in range(30)
    ]
    questionnaires = []
    for day, phq, gad in [
        ("2026-09-01", [2, 2, 2, 2, 1, 1, 1, 1, 0], [2, 2, 2, 2, 1, 1, 1]),
        ("2026-09-15", [1, 1, 2, 1, 1, 1, 1, 0, 0], [2, 1, 1, 1, 1, 1, 1]),
        ("2026-09-30", [1, 1, 1, 1, 0, 0, 1, 0, 0], [1, 1, 1, 1, 0, 0, 1]),
    ]:
        for scale, values in [("phq9", phq), ("gad7", gad)]:
            questionnaires.append(dict(day=day, scale=scale, values=values))
    return dict(
        scenario_id=SCENARIO,
        provenance="Полностью вымышлено и создано программно; не производное от карты человека.",
        patient=dict(
            name="Мария Соколова",
            age=32,
            sex="female",
            synthetic=True,
            complaint="Около месяца беспокоят засыпание, усталость, тревога и снижение интереса к привычным делам.",
            treatment="Текущее лечение, диагнозы и лекарства в сценарии не заданы.",
        ),
        observations=observations,
        questionnaires=questionnaires,
    )


SCENARIOS = {SCENARIO: maria}


def trusted(scenario_id=SCENARIO):
    # No files, user-entered JSON, IDs of real patients, or arbitrary import adapters.
    return SCENARIOS[scenario_id]()
