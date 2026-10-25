"""Four independent, evidence-bound research directions. No prescribing output."""

from statistics import mean
from datetime import date
from app.scales import SCALES
from app.experimental_ai.scenarios import digest
from app.services import aviandr_data, aviandr_sources, drug_library

VERSION = "aviandr-applicability-v1"
INDICATIONS = {
    "match": "Соответствие подтверждённым сведениям обнаружено",
    "no_match": "Соответствие не установлено",
    "clarify": "Требуется уточнение врача",
    "unknown": "Недостаточно проверенных данных",
}
INTERACTIONS = {
    "contraindicated": "Обнаружено подтверждённое противопоказанное сочетание",
    "attention": "Обнаружено взаимодействие, требующее оценки врача",
    "not_found": "Подтверждённых взаимодействий в проверенных данных не обнаружено, полнота проверки ограничена",
    "unknown": "Недостаточно данных для оценки",
}


def source_info(source, locator="", quote=""):
    meta = source["meta"]
    return dict(
        id=source["id"],
        url=meta["source_url"],
        registration=meta["registration"],
        form=meta["product_form"],
        version=meta["version"],
        checked_on=meta["checked_on"],
        valid_until=meta["valid_until"],
        locator=locator,
        quote=quote,
        sha256=source["sha256"],
    )


def apply_rule(rule, facts):
    candidates = [
        f
        for f in facts
        if f["kind"] == rule["fact"] and f["key"].casefold() == rule["key"].casefold()
    ]
    if not candidates:
        return "unknown", []
    matched = []
    evaluated = []
    for fact in candidates:
        value = (
            fact["value"].get("value")
            if rule["fact"] == "age"
            else fact["value"].get("state")
        )
        if value in (None, "unknown"):
            continue
        try:
            match = (
                value == rule["expected"]
                if rule["operator"] == "equals"
                else (
                    float(value) < float(rule["expected"])
                    if rule["operator"] == "lt"
                    else float(value) >= float(rule["expected"])
                )
            )
        except (ValueError, TypeError):
            continue
        evaluated.append(fact)
        if match:
            matched.append(fact)
    if any(f["confirmed"] for f in matched):
        if any(f["confirmed"] and f not in matched for f in evaluated):
            return "conflict", evaluated
        return "confirmed", matched
    if matched:
        return "reported", matched
    return (
        "not_found"
        if any(
            f["confirmed"]
            and f["value"].get("state", f["value"].get("value"))
            not in (None, "unknown")
            for f in evaluated
        )
        else "unknown"
    ), candidates


def build(c, path, user, pid):
    data = aviandr_data.collect(c, path, user, pid)
    rules = aviandr_sources.rules(c)
    official = aviandr_sources.active_sources(c)
    evidence = []
    gaps = []

    def gap(key, question, reason, refs=()):
        if not any(g["id"] == key for g in gaps):
            gaps.append(
                dict(id=key, question=question, reason=reason, evidence_ids=list(refs))
            )

    def finding(direction, text, level="unknown", patient=(), source=None):
        value = dict(
            id=f"A{len(evidence)+1}",
            direction=direction,
            text=text,
            level=level,
            patient=list(patient),
            source=source,
        )
        evidence.append(value)
        return value

    results = []
    for rule in rules:
        state, patient = apply_rule(rule, data["facts"])
        results.append(dict(rule=rule, state=state, patient=patient))
        if state in {"unknown", "conflict"} and rule["kind"] != "adverse":
            gap(
                f"rule-{rule['id']}",
                f"Уточнено ли: {rule['label']}?",
                (
                    "Подтверждённые записи противоречат друг другу."
                    if state == "conflict"
                    else "Для проверенного правила нет подтверждённого значения в карте."
                ),
            )
    ind = [r for r in results if r["rule"]["kind"] == "indication"]
    confirmed_ind = any(r["state"] == "confirmed" for r in ind)
    ind_status = (
        "match"
        if confirmed_ind
        else (
            "unknown"
            if not ind
            else (
                "clarify"
                if any(r["state"] in {"reported", "unknown", "conflict"} for r in ind)
                else "no_match"
            )
        )
    )
    for r in ind:
        rule = r["rule"]
        finding(
            "indications",
            rule["label"],
            (
                "favorable"
                if r["state"] == "confirmed"
                else (
                    "attention" if r["state"] in {"reported", "conflict"} else "unknown"
                )
            ),
            r["patient"],
            source_info(rule["source"], rule["locator"], rule["quote"]),
        )
    if not ind:
        finding(
            "indications",
            "Нет действующего проверенного правила сопоставления показаний. Жалобы и баллы скрининга сами по себе не подтверждают зарегистрированное показание.",
        )
        gap(
            "indication-source",
            "Какие зарегистрированные показания подтверждены актуальной инструкцией выбранной формы?",
            "Нет проверенного исполняемого правила показаний.",
        )
    if not any(f["kind"] == "diagnosis" and f["confirmed"] for f in data["facts"]):
        completed = [q for q in data["questionnaires"] if q["total"] is not None]
        last = completed[-1] if completed else None
        detail = (
            f" Последний {last['scale'].upper()}: {last['total']}, период {last['period_start']} — {last['period_end']}; это скрининг, не диагноз."
            if last
            else ""
        )
        gap(
            "diagnosis",
            "Есть ли ранее установленный врачом диагноз и документ, который его подтверждает?"
            + detail,
            "В карте нет подтверждённой записи диагноза.",
        )
    trajectories = []
    for scale in ("phq4", "phq9", "gad7"):
        for version in sorted(
            {q["version"] for q in data["questionnaires"] if q["scale"] == scale}
        ):
            points = [
                q
                for q in data["questionnaires"]
                if q["scale"] == scale
                and q["version"] == version
                and q["total"] is not None
            ]
            if points:
                trajectories.append(
                    dict(
                        scale=scale,
                        version=version,
                        points=[
                            dict(id=q["id"], day=q["period_end"], total=q["total"])
                            for q in points
                        ],
                        delta=(
                            points[-1]["total"] - points[0]["total"]
                            if len(points) > 1
                            else None
                        ),
                    )
                )
                trajectory = trajectories[-1]
                valid = []
                for point in trajectory["points"]:
                    try:
                        valid.append((date.fromisoformat(point["day"]), point["total"]))
                    except ValueError:
                        continue
                if valid:
                    first_day, last_day = valid[0][0], valid[-1][0]
                    span = max(1, (last_day - first_day).days)
                    maximum = SCALES[scale]["max"]
                    trajectory["chart"] = dict(
                        first=first_day.isoformat(),
                        last=last_day.isoformat(),
                        maximum=maximum,
                        points=" ".join(
                            f"{30+700*(day-first_day).days/span:.2f},{145-120*value/maximum:.2f}"
                            for day, value in valid
                        ),
                    )
                finding(
                    "indications",
                    f"{scale.upper()}: сохранённые полные суммы {points[0]['total']} → {points[-1]['total']}; {len(points)} наблюдений одной версии. Изменение балла не устанавливает диагноз или эффект лечения.",
                    "unknown",
                    points,
                )
    contra = [
        r for r in results if r["rule"]["kind"] in {"contraindication", "caution"}
    ]
    critical = any(
        r["rule"]["kind"] == "contraindication" and r["state"] == "confirmed"
        for r in contra
    )
    caution = any(r["state"] in {"confirmed", "reported", "conflict"} for r in contra)
    missing = not contra or any(r["state"] in {"unknown", "conflict"} for r in contra)
    contra_status = (
        "Подтверждённое противопоказание"
        if critical
        else (
            "Фактор, требующий осторожности / уточнения"
            if caution
            else (
                "Неизвестные или непроверенные факторы"
                if missing
                else "Совпадений в имеющихся данных не обнаружено; проверка неполная"
            )
        )
    )
    for r in contra:
        rule = r["rule"]
        label = {
            "confirmed": "Подтверждённый фактор",
            "reported": "Со слов, требует уточнения",
            "unknown": "Нет проверенного значения",
            "not_found": "Совпадение не обнаружено",
            "conflict": "Подтверждённые записи противоречат друг другу",
        }[r["state"]]
        finding(
            "contraindications",
            f"{rule['label']}: {label}",
            (
                "critical"
                if rule["kind"] == "contraindication" and r["state"] == "confirmed"
                else (
                    "attention"
                    if r["state"] in {"reported", "confirmed", "conflict"}
                    else "unknown"
                )
            ),
            r["patient"],
            source_info(rule["source"], rule["locator"], rule["quote"]),
        )
    if not contra:
        finding(
            "contraindications",
            "Официальные противопоказания и предупреждения не превращены в действующие проверенные правила. Отсутствие записи в анамнезе не означает отсутствие противопоказания.",
        )
        gap(
            "contra-source",
            "Сверены ли противопоказания и ограничения с актуальной инструкцией и полным анамнезом?",
            "Проверенных правил противопоказаний и предупреждений нет.",
        )
    if not any(f["kind"] == "age" and f["confirmed"] for f in data["facts"]):
        gap(
            "age",
            "Какой возраст пациента подтверждён, и на какую дату?",
            "Возраст не подтверждён в структурированной записи врача.",
        )
    current = [
        t for t in data["therapy"] if t["state"] in {"current", "prescribed", "unknown"}
    ]
    av = next((t for t in data["therapy"] if t["code"] == "D01"), None)
    phase = (
        "current"
        if av and av["state"] == "current" and av["confirmed"]
        else (
            "not_current"
            if av and av["state"] in {"past", "considered"} and av["confirmed"]
            else "unknown"
        )
    )
    others = [t for t in current if t["code"] != "D01"]
    pairs = []
    sources = drug_library.sources(c)
    for drug in others:
        key = "|".join(sorted(("D01", drug["code"])))
        known = []
        if (
            drug.get("confirmed")
            and drug.get("identity_confirmed")
            and drug["state"] == "current"
        ):
            for source in sources:
                if source["meta"]["drug_code"] == "D01" and source["id"] not in {
                    s["id"] for s in official
                }:
                    continue
                known.extend(
                    dict(
                        level=p["level"],
                        text=p["explanation"],
                        source=source_info(source, p["locator"], p["explanation"]),
                    )
                    for p in source["meta"]["pairs"]
                    if p["key"] == key
                )
        state = (
            "contraindicated"
            if any(k["level"] == "contraindicated" for k in known)
            else (
                "attention"
                if known
                else (
                    "unknown"
                    if not drug.get("confirmed")
                    or not drug.get("identity_confirmed")
                    or drug["state"] != "current"
                    else "not_found"
                )
            )
        )
        pairs.append(
            dict(drug=drug, status=INTERACTIONS[state], state=state, known=known)
        )
        finding(
            "interactions",
            f"{drug['name']}: {INTERACTIONS[state]}",
            (
                "critical"
                if state == "contraindicated"
                else "attention" if state == "attention" else "unknown"
            ),
            [drug],
            known[0]["source"] if known else None,
        )
        if not known:
            gap(
                f"interaction-{drug['code']}",
                f"Подтверждены ли точное название, МНН и действующая инструкция для сочетания с {drug['name']}?",
                "Пара не проверена либо идентификация терапии не подтверждена.",
            )
    interaction_state = (
        "contraindicated"
        if any(p["state"] == "contraindicated" for p in pairs)
        else (
            "attention"
            if any(p["state"] == "attention" for p in pairs)
            else (
                "unknown"
                if not pairs or any(p["state"] == "unknown" for p in pairs)
                else "not_found"
            )
        )
    )
    if not others:
        finding(
            "interactions",
            "Другие текущие препараты не подтверждены. Пустой список не означает отсутствия терапии.",
        )
        gap(
            "medications",
            "Какие лекарства, безрецептурные средства и добавки фактически принимаются сейчас?",
            "Нет подтверждённого полного списка текущей терапии.",
        )
    reports = [r for r in data["reactions"] if r["code"] == "D01"]
    for report in reports:
        applicable = [
            r
            for r in results
            if r["rule"]["kind"] == "adverse"
            and r["rule"]["key"].casefold() == report["medical"].casefold()
        ]
        finding(
            "tolerability",
            f"{'Пациент сообщил' if report['author_role']=='patient' else 'Врач записал наблюдение'}: {report['label']}; начало {report['onset'] or 'не уточнено'}. Причинность не устанавливается по времени.",
            "attention",
            [
                dict(
                    id=report["id"],
                    relation=report["relation"],
                    reviews=report["reviews"],
                )
            ],
            (
                source_info(
                    applicable[0]["rule"]["source"],
                    applicable[0]["rule"]["locator"],
                    applicable[0]["rule"]["quote"],
                )
                if applicable
                else None
            ),
        )
        if not applicable:
            gap(
                f"reaction-{report['id']}",
                f"Как развивалось проявление «{report['label']}» с {report['onset'] or 'неизвестной даты'}, и каковы альтернативные объяснения?",
                "Нет проверенного сопоставления с разделом нежелательных реакций.",
            )
    events = [
        f
        for f in data["facts"]
        if f["kind"] == "intake_event"
        and f["key"] == "D01"
        and data["period_start"] <= f["day"] <= data["period_end"]
    ]
    start = av.get("start", "") if av else ""
    if start:
        try:
            if date.fromisoformat(start) > date.fromisoformat(data["period_end"]):
                raise ValueError()
        except ValueError:
            gap(
                "start-date-unclear",
                f"Какова точная дата фактического начала? В карте записано: {start}.",
                "Приблизительная или будущая дата не используется как точная граница сравнения.",
            )
            start = ""
    expected_days = None
    outside_course = []
    if phase == "current" and start:
        expected_days = max(
            0,
            (
                date.fromisoformat(data["period_end"])
                - max(
                    date.fromisoformat(start), date.fromisoformat(data["period_start"])
                )
            ).days
            + 1,
        )
        outside_course = [f for f in events if f["day"] < start]
        events = [f for f in events if f["day"] >= start]
        if outside_course:
            gap(
                "intake-date-conflict",
                f"Почему есть отметка приёма {outside_course[0]['day']} до записанного начала {start}?",
                "Даты фактического приёма и врачебной записи не совпадают.",
            )
    adherence = {
        s: sum(f["value"]["state"] == s for f in events)
        for s in ("taken", "missed", "unknown")
    }
    adherence["unrecorded"] = (
        expected_days if expected_days is not None else 30
    ) - len(events)
    adherence["expected_days"] = expected_days
    adherence["before_course_days"] = (
        30 - expected_days if expected_days is not None else None
    )
    adherence["missed_reasons"] = [
        dict(day=f["day"], reason=f["value"].get("reason", "") or "Причина не записана")
        for f in events
        if f["value"]["state"] == "missed"
    ]
    latest_diary = {r["day"]: r for r in data["diary"]}
    changes = {}
    if phase == "current" and start:
        for field in ("wellbeing", "sleep", "sleep_quality"):
            before = [
                r[field]
                for day, r in latest_diary.items()
                if day < start and r[field] is not None
            ]
            after = [
                r[field]
                for day, r in latest_diary.items()
                if day >= start and r[field] is not None
            ]
            changes[field] = dict(
                before=round(mean(before), 2) if before else None,
                after=round(mean(after), 2) if after else None,
                n_before=len(before),
                n_after=len(after),
            )
    if phase != "current":
        tolerance_status = "Индивидуальная переносимость пока неизвестна"
        gap(
            "exposure",
            "Принимал ли пациент Авиандр ранее, или он только рассматривается? Каков документированный опыт?",
            "Фактический текущий приём не подтверждён.",
        )
    else:
        tolerance_status = (
            "Зарегистрированы проявления; требуется оценка врача"
            if reports
            else "Данные наблюдения есть; индивидуальный вывод ограничен"
        )
        if not start:
            gap(
                "start",
                "Когда фактически начат приём, и совпадает ли эта дата с датой назначения?",
                "Дата начала текущего приёма не подтверждена.",
            )
        if not events or adherence["missed"]:
            missed = (
                adherence["missed_reasons"][0] if adherence["missed_reasons"] else None
            )
            gap(
                "adherence",
                (
                    f"Чем объясняется пропуск {missed['day']} ({missed['reason']})?"
                    if missed
                    else "Какие фактические отметки приёма и пропусков можно подтвердить?"
                ),
                "Общие отметки дневника не приписываются конкретному лекарству.",
            )
    if data["preference_choices"]:
        primary = next(
            (p for p in data["preference_choices"] if p["primary"]),
            data["preference_choices"][0],
        )
        gap(
            "preference",
            f"Почему для пациента особенно важно избежать «{primary['label']}» и был ли такой опыт ранее?",
            "Учтён главный приоритет из отправленных пожеланий.",
        )
    else:
        gap(
            "preference",
            "Каких нежелательных эффектов пациент хотел бы избежать и были ли реакции на прошлое лечение?",
            "Отправленные пожелания отсутствуют.",
        )
    gap(
        "confounders",
        "Какие изменения сна, нагрузки, других препаратов или значимые события совпали с наблюдаемой динамикой?",
        "Изменения во времени не доказывают эффект или причинность.",
    )
    gap(
        "coverage",
        f"Достаточно ли подтверждённых сведений и наблюдений за {data['period_start']} — {data['period_end']} для решения врача?",
        "Проверка охватывает только доступные записи и подтверждённые правила.",
    )
    preference_assessment = []
    for choice in data["preference_choices"]:
        known = [
            r
            for r in rules
            if r["kind"] == "adverse"
            and r["key"].casefold() == choice["medical"].casefold()
        ]
        preference_assessment.append(
            dict(
                **choice,
                status=(
                    "Термин есть в проверенном разделе инструкции; это не прогноз личной реакции"
                    if known
                    else "Проверенного сопоставления нет"
                ),
                sources=[
                    source_info(r["source"], r["locator"], r["quote"]) for r in known
                ],
            )
        )
    finding(
        "tolerability",
        tolerance_status
        + f". За 30 дней: отметок приёма {adherence['taken']}, пропусков {adherence['missed']}, без записи {adherence['unrecorded']}. Отметки за день не являются подсчётом отдельных доз.",
        "attention" if reports else "unknown",
        [
            dict(
                phase=phase,
                start=start,
                adherence=adherence,
                changes=changes,
                preferences=preference_assessment,
            )
        ],
    )
    directions = [
        dict(
            key="indications",
            title="Показания",
            status=INDICATIONS[ind_status],
            level=(
                "favorable"
                if ind_status == "match"
                else "attention" if ind_status == "clarify" else "unknown"
            ),
        ),
        dict(
            key="contraindications",
            title="Противопоказания",
            status=contra_status,
            level="critical" if critical else "attention" if caution else "unknown",
        ),
        dict(
            key="interactions",
            title="Сочетаемость",
            status=INTERACTIONS[interaction_state],
            level=(
                "critical"
                if interaction_state == "contraindicated"
                else "attention" if interaction_state == "attention" else "unknown"
            ),
        ),
        dict(
            key="tolerability",
            title="Переносимость",
            status=tolerance_status,
            level="attention" if reports else "unknown",
        ),
    ]
    for direction in directions:
        direction["findings"] = [
            e for e in evidence if e["direction"] == direction["key"]
        ]
    latest_scores = {}
    for q in data["questionnaires"]:
        if q["total"] is not None and q["scale"] in {"phq4", "phq9", "gad7"}:
            latest_scores[q["scale"]] = (
                f"{q['scale'].upper()}: {q['total']} ({q['period_end']})"
            )
    age_facts = [f for f in data["facts"] if f["kind"] == "age"]
    age = next(
        (f for f in age_facts if f["confirmed"]), age_facts[0] if age_facts else None
    )
    directions[0]["preview"] = (
        "Последние полные анкеты: " + "; ".join(latest_scores.values())
        if latest_scores
        else "Полных результатов анкет пока нет."
    )
    directions[1]["preview"] = (
        f"Возраст: {age['value']['value']} · {'запись проверена' if age['confirmed'] else 'со слов пациента'}. "
        if age
        else "Возраст не уточнён. "
    ) + f"Проверенных правил ограничений: {len(contra)}."
    directions[2]["preview"] = (
        "В текущих сведениях: " + ", ".join(t["name"] for t in others[:3])
        if others
        else "Другие текущие препараты не уточнены; пустой список не считается отрицательным ответом."
    )
    directions[3]["preview"] = (
        f"Отметок приёма: {adherence['taken']}; пропусков: {adherence['missed']}; сообщений о проявлениях: {len(reports)}."
        if phase == "current"
        else (
            "Препарат рассматривается врачом; фактического начала в карте нет."
            if av and av["state"] == "considered"
            else "Текущий приём не подтверждён; требуется уточнить предыдущий опыт."
        )
    )
    result = dict(
        version=VERSION,
        registration=aviandr_sources.registration(),
        synthetic_only=True,
        directions=directions,
        evidence=evidence,
        gaps=gaps[:7],
        all_gaps=gaps,
        data=data,
        pairs=pairs,
        phase=phase,
        therapy_start=start,
        adherence=adherence,
        changes=changes,
        trajectories=trajectories,
        preference_assessment=preference_assessment,
        sources=[source_info(s) for s in official],
        rules_count=len(rules),
        limitations=[
            "Исследовательская сверка, не рекомендация начать препарат и не заключение о безопасности.",
            "Показания не выводятся из баллов скрининга; источник, подтверждение факта и полнота проверяются отдельно.",
            "Сохранённый или несохранённый личный черновик не считается текущей терапией.",
            "Совпадение по времени не устанавливает причинность; отсутствие записи не исключает реакцию.",
            "Редакционные карточки и матрицы не становятся верифицированными правилами автоматически.",
        ],
    )
    result["checksum"] = digest(result)
    return result
