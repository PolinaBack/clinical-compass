"""Patient-specific descriptions of computed facts, never new medical rules."""

from app.scales import SCALES
from app.intake_catalog import BY_KEY
from app.services.clinical_forms import visible
from app.services.patient_ai_data import text

VERSION = "aviandr-patient-conclusions-v1"
LABELS = {"phq4": "PHQ-4", "phq9": "PHQ-9", "gad7": "GAD-7"}
FIELD_LABELS = {
    "onset": "начало симптомов",
    "frequency": "частота проявлений",
    "impact": "влияние на повседневную жизнь, 0–10",
    "other": "другие жалобы",
    "mood": "самооценка настроения, 0–10",
    "anxiety": "самооценка тревоги, 0–10",
    "panic": "периоды выраженной тревоги или паники",
    "energy": "изменение энергии",
    "known_conditions": "сообщённые заболевания",
    "systems": "отмеченные системы органов",
    "description": "описание состояния здоровья",
    "current": "сведения о текущих препаратах",
    "supplements": "сведения о добавках",
}


def short(value, limit=170):
    value = text(value, limit + 1)
    return value if len(value) <= limit else value[:limit].rsplit(" ", 1)[0] + "…"


def names(items, limit=3):
    shown = [short(item["name"], 80) for item in items[:limit]]
    if len(items) > limit:
        shown.append(f"ещё {len(items)-limit} — полный список в подробностях")
    return ", ".join(shown)


class Context:
    def __init__(self, report, pid):
        self.report = report
        self.data = report["data"]
        self.pid = pid
        self.evidence = []
        self.questions = []

    def fact(self, direction, key, description, origin, *, values=None, href=None):
        result = dict(
            id=f"P-{direction}-{key}",
            direction=direction,
            kind="patient_data",
            text=description,
            level="unknown",
            origin=origin,
            href=href or f"/patients/{self.pid}/profile",
            values=values,
            patient=[],
            source=None,
        )
        self.evidence.append(result)
        return result

    def question(self, direction, key, question, reason, basis):
        result = dict(
            id=f"patient-{direction}-{key}",
            direction=direction,
            question=question,
            reason=reason,
            evidence_ids=[entry["id"] for entry in basis],
        )
        self.questions.append(result)
        return result

    def facts(self, kind):
        return [f for f in self.data["facts"] if f["kind"] == kind]

    def answers(self, section, keys):
        result = []
        for row in self.data["reported"]:
            if row.get("section") != section:
                continue
            for field in BY_KEY[section]["fields"]:
                if field["key"] not in keys or not visible(field, row["answers"]):
                    continue
                answer = row["answers"].get(field["key"], {})
                value = answer.get("value")
                if answer.get("status") != "answered" or value in (None, "", []):
                    continue
                display = (
                    ", ".join(map(str, value))
                    if isinstance(value, list)
                    else str(value)
                )
                result.append(
                    dict(
                        field=FIELD_LABELS.get(field["key"], field["label"]),
                        original_field=field["label"],
                        value=value,
                        text=short(display, 120),
                        source=row["source"],
                    )
                )
        return result

    def age(self):
        facts = self.facts("age")
        verified = sorted({f["value"]["value"] for f in facts if f["confirmed"]})
        if len(verified) > 1:
            return (
                "Возраст в проверенных записях расходится: "
                + ", ".join(map(str, verified))
                + " лет"
            )
        if verified:
            return f"Возраст по подтверждённой записи врача: {verified[0]}"
        reported = sorted({f["value"]["value"] for f in facts})
        if reported:
            return (
                "Возраст со слов: "
                + ", ".join(map(str, reported))
                + " лет; врачебной проверки нет"
            )
        return "Возраст не уточнён"

    def scores(self):
        result = []
        for scale in ("gad7", "phq9", "phq4"):
            candidates = [t for t in self.report["trajectories"] if t["scale"] == scale]
            if not candidates:
                continue
            row = max(
                candidates,
                key=lambda t: (t["points"][-1]["day"], t["points"][-1]["id"]),
            )
            first, last = row["points"][0], row["points"][-1]
            result.append(
                dict(
                    scale=scale,
                    text=f"{LABELS[scale]}: {first['total']} → {last['total']} из {SCALES[scale]['max']} (полных измерений: {len(row['points'])}, одна версия, последнее {last['day']})",
                    points=row["points"],
                    version=row["version"],
                )
            )
        return result

    def packet(self, direction, conclusion, basis, unknowns, questions):
        sources = []
        for finding in direction["findings"]:
            source = finding.get("source")
            if source and source not in sources:
                sources.append(source)
        if not sources:
            sources = self.report["sources"]
        return dict(
            conclusion=conclusion,
            basis=[
                {k: entry[k] for k in ("id", "text", "origin", "href")}
                for entry in basis
            ],
            unknowns=unknowns,
            questions=questions,
            sources=sources,
        )


def indications(context, direction):
    data = context.data
    complaints = []
    for row in data["reported"]:
        if row.get("section") == "complaints":
            field = row["answers"].get("groups", {})
            if field.get("status") == "answered":
                value = field.get("value", [])
                complaints.extend(value if isinstance(value, list) else [value])
    profile = data["profile"].get("goals", "")
    complaint = (
        f"В профиле записано: «{short(profile.split('. ')[0].rstrip('.!?'))}»"
        if profile
        else (
            "В отправленном анамнезе отмечено: «"
            + ", ".join(map(str, complaints))
            + "»"
            if complaints
            else "Сохранённого описания жалоб пока нет"
        )
    )
    if profile and complaints:
        complaint += (
            "; в отправленном анамнезе отмечено «"
            + ", ".join(map(str, complaints))
            + "»"
        )
    symptom_answers = context.answers(
        "complaints",
        {f"complaint_{i}" for i in range(1, 10)}
        | {"other", "onset", "frequency", "impact"},
    )
    symptom_answers += context.answers("mental", {"mood", "anxiety", "panic", "energy"})
    if symptom_answers:
        complaint += "; по самоотчёту: " + "; ".join(
            f"{a['field']} — «{a['text']}»" for a in symptom_answers[:2]
        )
    context_fact = context.fact(
        "indications",
        "context",
        complaint,
        "Профиль и отправленный анамнез",
        values=dict(
            goals=profile, complaints=complaints, symptom_answers=symptom_answers
        ),
    )
    scores = context.scores()
    score_text = (
        "; ".join(s["text"] for s in scores[:2])
        if scores
        else "Полных PHQ-4/PHQ-9/GAD-7 нет; пропуски не считаются нулевыми баллами"
    )
    score_fact = context.fact(
        "indications",
        "scores",
        score_text,
        "Завершённые анкеты и исходные ответы",
        values=scores,
        href=f"/patients/{context.pid}/workspace/assessments",
    )
    diagnoses = context.facts("diagnosis")
    confirmed = [
        f["key"]
        for f in diagnoses
        if f["confirmed"] and f["value"].get("state") == "present"
    ]
    diagnosis_text = (
        "Врачебная запись диагноза: " + ", ".join(confirmed)
        if confirmed
        else "Подтверждённой записи установленного врачом диагноза нет"
    )
    reported_diagnoses = context.answers("mental", {"past_diagnosis"})
    if not confirmed and reported_diagnoses:
        diagnosis_text = (
            "В анамнезе со слов записано «"
            + reported_diagnoses[0]["text"]
            + "»; врачебной проверки диагноза нет"
        )
    diagnosis_fact = context.fact(
        "indications",
        "diagnosis",
        diagnosis_text,
        "Структурированные сведения врача / проверка анамнеза",
        values=dict(clinician_records=diagnoses, reported=reported_diagnoses),
    )
    clinical = [f for f in direction["findings"] if f["source"]]
    if not clinical:
        source_sentence = "Сопоставить эту картину с зарегистрированными показаниями пока нельзя: действующего проверенного правила показаний нет."
    else:
        source_sentence = f"Результат сверки с проверенными правилами: «{direction['status']}»; основание и цитата доступны в подробностях."
    conclusion = [
        complaint + ".",
        score_text + "; сами баллы не устанавливают диагноз.",
        diagnosis_text + ".",
        source_sentence,
    ]
    unknowns = (
        [
            "Проверенный перечень показаний выбранной формы не подключён как исполняемое правило."
        ]
        if not clinical
        else [
            "Сверка ограничена перечисленными проверенными правилами и не подтверждает целесообразность назначения."
        ]
    )
    if not confirmed:
        unknowns.append(
            "Есть ли установленный врачом диагноз, его дата и подтверждающий документ."
        )
    if not scores:
        unknowns.append(
            "Полные ответы и повторные измерения симптомов за сопоставимые периоды."
        )
    questions = [
        context.question(
            "indications",
            "diagnosis",
            (
                f"Каким документом и датой подтверждена запись «{', '.join(confirmed)}»?"
                if confirmed
                else "Есть ли установленный врачом диагноз, который объясняет записанные жалобы, и где его подтверждение?"
            ),
            diagnosis_text,
            [context_fact, diagnosis_fact],
        ),
        context.question(
            "indications",
            "context",
            (
                f"Как записанная динамика {LABELS[scores[0]['scale']]} соотносится с повседневным состоянием и жалобами пациента?"
                if scores
                else "Как давно сохраняются описанные жалобы и какие полные анкеты доступны для исходной оценки?"
            ),
            "Скрининг и описание жалоб не подменяют клиническую проверку.",
            [context_fact, score_fact],
        ),
    ]
    return context.packet(
        direction,
        conclusion,
        [context_fact, score_fact, diagnosis_fact],
        unknowns,
        questions,
    )


def contraindications(context, direction):
    age = context.age()
    ages = context.facts("age")
    factors = context.facts("factor")
    allergies = context.facts("allergy")
    age_fact = context.fact(
        "contraindications",
        "age",
        age,
        "Врачебные сведения и дата рождения из отправленного анамнеза",
        values=ages,
    )

    def describe(items, empty):
        if not items:
            return empty
        states = {
            "present": "отмечено",
            "absent": "отрицается",
            "unknown": "не уточнено",
        }
        return "; ".join(
            f"{short(f['key'], 90)} — {states.get(f['value'].get('state'), 'не уточнено')} ({'проверено врачом' if f['confirmed'] else 'со слов / не проверено'})"
            for f in items[:3]
        )

    allergy_text = describe(
        allergies,
        "Структурированных сведений об аллергии нет; это не отрицательный ответ",
    )
    factor_text = describe(
        factors,
        "Сопутствующие заболевания и факторы в структурированных записях не уточнены",
    )
    reported_reactions = context.answers("medicines", {"reactions"})
    reported_factors = context.answers(
        "health", {"known_conditions", "systems", "description"}
    )
    if not allergies and reported_reactions:
        allergy_text = f"О реакциях на лекарства пациент ответил «{reported_reactions[0]['text']}»; это самоотчёт, тип и причина требуют проверки"
    if not factors and reported_factors:
        factor_text = (
            "По самоотчёту о здоровье: "
            + "; ".join(f"{a['field']} — «{a['text']}»" for a in reported_factors[:2])
            + "; врачебная проверка не внесена"
        )
    allergy_fact = context.fact(
        "contraindications",
        "allergies",
        allergy_text,
        "Отправленный анамнез и врачебные уточнения",
        values=dict(clinician_records=allergies, reported=reported_reactions),
    )
    factor_fact = context.fact(
        "contraindications",
        "factors",
        factor_text,
        "Карточки заболеваний и структурированные сведения врача",
        values=dict(clinician_records=factors, reported=reported_factors),
    )
    clinical = [f for f in direction["findings"] if f["source"]]
    conclusion = [
        age + ".",
        allergy_text + "; " + factor_text[0].lower() + factor_text[1:] + ".",
    ]
    if clinical:
        conclusion.append(
            f"По проверенным правилам: «{direction['status']}»; конкретные факторы и источники показаны в подробностях."
        )
    else:
        conclusion.append(
            "Без проверенных правил ограничений нельзя исключить противопоказания по этой карте; отсутствие записи не означает безопасность."
        )
    unknowns = ["Полнота анамнеза и сведений, необходимых по актуальной инструкции."]
    if not clinical:
        unknowns.append(
            "Официальные противопоказания и предупреждения не подключены как проверенные исполняемые правила."
        )
    unknowns.extend(
        f["text"]
        for f in clinical
        if "Нет проверенного значения" in f["text"] or "противоречат" in f["text"]
    )
    if not allergies:
        unknowns.append(
            "Какие вещества вызывали аллергию или непереносимость и как именно проявлялась реакция."
        )
    if "расходится" in age or not any(f["confirmed"] for f in ages):
        unknowns.append(
            "Возраст требует подтверждения или устранения противоречия в записях."
        )
    questions = [
        context.question(
            "contraindications",
            "allergy",
            (
                f"Как проявлялась реакция на «{short(allergies[0]['key'], 80)}» и чем подтверждено её описание?"
                if allergies
                else f"Уточнён ли аллергологический анамнез при следующих данных: {age.lower()}?"
            ),
            allergy_text,
            [age_fact, allergy_fact],
        ),
        context.question(
            "contraindications",
            "factors",
            (
                f"Как подтверждён статус фактора «{short(factors[0]['key'], 80)}» и какие сведения о нём ещё нужны по инструкции?"
                if factors
                else "Какие сопутствующие заболевания и непереносимости нужно документировать, прежде чем сверять ограничения?"
            ),
            factor_text,
            [age_fact, factor_fact],
        ),
    ]
    if clinical:
        questions[1] = context.question(
            "contraindications",
            "verified-factor",
            f"Что ещё нужно проверить по записи «{short(clinical[0]['text'], 130)}» перед решением врача?",
            "Основание относится к конкретной проверенной цитате и данным этой карты.",
            [age_fact, clinical[0]],
        )
    return context.packet(
        direction,
        conclusion,
        [age_fact, allergy_fact, factor_fact],
        unknowns,
        questions,
    )


def interactions(context, direction):
    data, report = context.data, context.report
    current = [t for t in data["therapy"] if t["state"] == "current"]
    others = [t for t in current if t["code"] != "D01"]
    uncertain = [t for t in data["therapy"] if t["state"] in {"unknown", "prescribed"}]
    av = next((t for t in data["therapy"] if t["code"] == "D01"), None)
    phase = (
        "По записи врача Авиандр только рассматривается"
        if av and av["state"] == "considered" and av["confirmed"]
        else (
            "Текущий учебный приём Авиандра подтверждён записью врача"
            if report["phase"] == "current"
            else "Текущий приём Авиандра не подтверждён"
        )
    )
    current_text = (
        "В текущих записях: " + names(current)
        if current
        else "Текущие препараты не уточнены; пустой список не означает, что пациент ничего не принимает"
    )
    reported_current = context.answers("medicines", {"current", "supplements"})
    if not current and reported_current:
        current_text = (
            "Отправленные ответы о лекарствах: "
            + "; ".join(f"{a['field']} — «{a['text']}»" for a in reported_current)
            + "; точный текущий список врачом не подтверждён"
        )
    therapy_fact = context.fact(
        "interactions",
        "therapy",
        current_text,
        "Записи о фактическом приёме; назначение само по себе не подтверждает приём",
        values=data["therapy"],
        href=f"/patients/{context.pid}/assignments#medicines",
    )
    unidentified = [
        t for t in current if not t["confirmed"] or not t.get("identity_confirmed")
    ]
    identity_text = (
        "Требуют проверки названия/МНН: " + names(unidentified)
        if unidentified
        else (
            "Идентификация проверена для перечисленных текущих препаратов"
            if current
            else "Нет текущего препарата с подтверждёнными названием и МНН"
        )
    )
    if uncertain:
        identity_text += "; фактический статус не уточнён: " + names(uncertain)
    identity_fact = context.fact(
        "interactions",
        "identity",
        identity_text,
        "Отдельное подтверждение названия, МНН и фактического статуса",
        values=dict(unidentified=unidentified, uncertain=uncertain),
    )
    known = [p for p in report["pairs"] if p["known"]]
    unknown = [p for p in report["pairs"] if not p["known"]]
    pairs_text = f"Пар с проверенной записью: {len(known)}; без проверенной записи: {len(unknown)}"
    if not report["pairs"]:
        pairs_text = "Пары не сформированы: другие актуальные препараты не установлены"
    pair_fact = context.fact(
        "interactions",
        "pairs",
        pairs_text,
        "Точная идентификация и только верифицированные записи конкретных пар",
        values=[
            dict(code=p["drug"]["code"], state=p["state"]) for p in report["pairs"]
        ],
    )
    conclusion = [
        phase + ".",
        current_text + "; " + identity_text[0].lower() + identity_text[1:] + ".",
    ]
    if known:
        conclusion.append(
            f"Результат проверки конкретных пар: «{direction['status']}»; типы взаимодействий и цитаты приведены в подробностях."
        )
    elif report["pairs"]:
        conclusion.append(
            f"Для проверки сочетаний ({names([p['drug'] for p in unknown])}) нет достаточного проверенного основания; взаимодействия не подставляются из редакционной матрицы."
        )
    else:
        conclusion.append(
            "Проверить сочетания по неполному списку пока нельзя; отсутствие пары не подтверждает безопасность."
        )
    unknowns = [
        "Полнота списка фактически принимаемых лекарств, безрецептурных средств и добавок."
    ]
    if unidentified:
        unknowns.append(
            "Точное название и действующее вещество: " + names(unidentified) + "."
        )
    if unknown:
        unknowns.append(
            "Проверенные записи конкретных сочетаний отсутствуют: "
            + names([p["drug"] for p in unknown])
            + "."
        )
    if uncertain:
        unknowns.append(
            "Назначенный или сообщённый препарат ещё не подтверждён как фактически принимаемый: "
            + names(uncertain)
            + "."
        )
    questions = [
        context.question(
            "interactions",
            "identity",
            (
                f"Чем подтверждены фактический приём и МНН препарата «{short((others or current)[0]['name'], 80)}»?"
                if current
                else "Какие лекарства и добавки фактически принимаются сейчас, пока текущая терапия не уточнена?"
            ),
            identity_text,
            [therapy_fact, identity_fact],
        ),
        context.question(
            "interactions",
            "completeness",
            (
                f"Есть ли другие средства помимо {names(current, 2)}, включая нерегулярный приём и добавки?"
                if current
                else "Подтверждён ли полный список средств, который потребуется для проверки предполагаемых сочетаний с Авиандром?"
            ),
            "Пустая или частичная запись не подтверждает отсутствие другой терапии.",
            [therapy_fact, pair_fact],
        ),
    ]
    return context.packet(
        direction,
        conclusion,
        [therapy_fact, identity_fact, pair_fact],
        unknowns,
        questions,
    )


def tolerability(context, direction):
    data, report = context.data, context.report
    events = [
        f for f in data["facts"] if f["kind"] == "intake_event" and f["key"] == "D01"
    ]
    reports = [r for r in data["reactions"] if r["code"] == "D01"]
    a = report["adherence"]
    actual = report["phase"] == "current"
    av = next((t for t in data["therapy"] if t["code"] == "D01"), None)
    diary_days = [r for r in data["observations"] if r["diary_id"] is not None]
    latest = diary_days[-1] if diary_days else None
    first = (
        f"Текущий учебный приём подтверждён записью врача, начало {report['therapy_start']}"
        if actual and report["therapy_start"]
        else (
            "Текущий учебный приём подтверждён, точная дата начала не уточнена"
            if actual
            else (
                "По подтверждённой записи Авиандр только рассматривается, текущий приём не начат"
                if av and av["state"] == "considered" and av["confirmed"]
                else "Текущий приём не подтверждён; предыдущий опыт нужно уточнить отдельно"
            )
        )
    )
    count_text = (
        f"Дней с отметкой приёма: {a['taken']}; с пропуском: {a['missed']}; «не знаю»: {a['unknown']}; без записи: {a['unrecorded']}"
        if actual
        else f"Дневниковых дней в окне: {len(diary_days)}; без подтверждённого приёма это не данные индивидуальной переносимости"
    )
    intake_fact = context.fact(
        "tolerability",
        "intake",
        first + "; " + count_text[0].lower() + count_text[1:],
        "Врачебная запись и отдельные дневные отметки, не подсчёт доз",
        values=dict(
            phase=report["phase"],
            start=report["therapy_start"],
            adherence=a,
            entries=events,
        ),
        href=f"/patients/{context.pid}/assignments#medicines",
    )
    changes = report["changes"]
    compared = []
    for key, label in (("wellbeing", "самочувствие"), ("sleep", "сон, часы")):
        change = changes.get(key, {})
        if change.get("before") is not None and change.get("after") is not None:
            compared.append(f"{label}: {change['before']} → {change['after']}")
    dynamics = (
        "Средние до/после записанного начала: " + "; ".join(compared)
        if compared
        else (
            f"Последняя запись {latest['day']}: самочувствие {latest['wellbeing']}, сон {latest['sleep_hours']} ч"
            if latest
            else "Нет дневниковых значений для сравнения"
        )
    )
    dynamics_fact = context.fact(
        "tolerability",
        "dynamics",
        dynamics,
        "Дневник и даты наблюдений; описательное сравнение, без причинности",
        values=dict(changes=changes, latest=latest),
        href=f"/patients/{context.pid}/diary",
    )
    primary = next((p for p in data["preference_choices"] if p["primary"]), None)
    report_text = "Зарегистрировано проявлений: " + str(len(reports))
    if reports:
        report_text += " — " + ", ".join(
            short(r["label"], 70) + " (" + (r["onset"] or "дата неизвестна") + ")"
            for r in reports[:3]
        )
    else:
        report_text += "; отсутствие сообщения не подтверждает хорошую переносимость"
    preference = (
        f"Главный отправленный приоритет: «{primary['label']}»"
        if primary
        else "Главный отправленный приоритет нежелательных эффектов не указан"
    )
    reaction_fact = context.fact(
        "tolerability",
        "reactions",
        report_text + "; " + preference[0].lower() + preference[1:],
        "Сообщения пациента / наблюдения врача и явно отправленные пожелания",
        values=dict(reactions=reports, preferences=data["preference_choices"]),
        href=f"/patients/{context.pid}/assignments#medicines",
    )
    if actual:
        duration = (
            f"; период после начала — {a['expected_days']} дней"
            if a["expected_days"] is not None
            else ""
        )
        conclusion = [
            first + duration + ".",
            count_text + ".",
            report_text + ".",
            dynamics
            + "; совпадение во времени не доказывает эффект препарата или связь с проявлением.",
        ]
    else:
        conclusion = [
            first + "; индивидуальная переносимость пока неизвестна.",
            count_text + "; " + dynamics[0].lower() + dynamics[1:] + ".",
            "При последующем наблюдении полезно раздельно фиксировать фактический приём, сон, самочувствие, проявления и сопутствующие события; эти записи не заменяют решение врача.",
        ]
    unknowns = [
        "Причина изменений самочувствия и альтернативные объяснения проявлений не устанавливаются совпадением дат."
    ]
    if not actual:
        unknowns.insert(
            0, "Подтверждённый опыт приёма и индивидуальная реакция на препарат."
        )
    elif not report["therapy_start"]:
        unknowns.insert(0, "Точная дата фактического начала для сравнения до/после.")
    if a["missed"]:
        unknowns.append(
            "Все обстоятельства пропусков; записанные причины приведены отдельно."
        )
    if reports and not all(r.get("relation") == "doctor_attributed" for r in reports):
        unknowns.append(
            "Отдельная обоснованная врачебная оценка связи зарегистрированных проявлений."
        )
    unknowns.append(
        "Сопоставление сообщений и пожеланий с актуальным проверенным разделом инструкции, если правила не подключены."
    )
    questions = []
    if reports:
        r = reports[0]
        questions.append(
            context.question(
                "tolerability",
                "reaction",
                f"Как развивалось проявление «{short(r['label'], 80)}» с {r['onset'] or 'неуточнённой даты'} и какие другие события совпали по времени?",
                "Сообщение о проявлении и врачебная оценка связи — разные сведения.",
                [intake_fact, reaction_fact],
            )
        )
    elif actual:
        questions.append(
            context.question(
                "tolerability",
                "observation",
                f"Какие изменения самочувствия и новые проявления отмечались с {report['therapy_start'] or 'неуточнённой даты начала'}, пока отдельных сообщений о реакции нет?",
                "Подтверждённый приём сам по себе не подтверждает хорошую переносимость.",
                [intake_fact, dynamics_fact, reaction_fact],
            )
        )
    else:
        questions.append(
            context.question(
                "tolerability",
                "experience",
                (
                    "Был ли опыт приёма Авиандра до его текущего рассмотрения и где описана переносимость?"
                    if av and av["state"] == "considered"
                    else "Когда-либо принимался ли Авиандр и можно ли подтвердить даты и опыт, пока текущий приём не установлен?"
                ),
                first,
                [intake_fact, dynamics_fact],
            )
        )
    if a["missed"]:
        missed = a["missed_reasons"][0]
        questions.append(
            context.question(
                "tolerability",
                "missed",
                f"Что привело к пропуску {missed['day']} при записанной причине «{short(missed['reason'], 100)}», и какие ещё обстоятельства стоит уточнить?",
                "Учитываются конкретные отметки дней; отсутствие записи не считается пропуском.",
                [intake_fact],
            )
        )
    questions.append(
        context.question(
            "tolerability",
            "preference",
            (
                f"Почему особенно важно избежать «{primary['label']}» и был ли такой опыт ранее?"
                if primary
                else "Какие нежелательные эффекты пациенту особенно важно избежать, пока отправленный главный приоритет не указан?"
            ),
            preference,
            [reaction_fact],
        )
    )
    return context.packet(
        direction,
        conclusion,
        [intake_fact, dynamics_fact, reaction_fact],
        unknowns,
        questions[:3],
    )


BUILDERS = dict(
    indications=indications,
    contraindications=contraindications,
    interactions=interactions,
    tolerability=tolerability,
)


def attach(report, pid):
    context = Context(report, pid)
    for direction in report["directions"]:
        direction["narrative"] = BUILDERS[direction["key"]](context, direction)
    # Concrete patient facts come first for explanatory grounding. Existing
    # rule/source evidence and its identifiers remain available, unchanged.
    report["evidence"] = context.evidence + report["evidence"]
    for direction in report["directions"]:
        direction["findings"] = [
            e for e in context.evidence if e["direction"] == direction["key"]
        ] + direction["findings"]
    selected = [d["narrative"]["questions"][0] for d in report["directions"]]
    for gap in report["all_gaps"]:
        if gap["id"].startswith("rule-") and not gap["evidence_ids"]:
            label = gap["question"].removeprefix("Уточнено ли: ").removesuffix("?")
            gap["evidence_ids"] = [
                e["id"]
                for e in report["evidence"]
                if e["source"]
                and (e["text"] == label or e["text"].startswith(label + ": "))
            ]
    priority_gaps = [
        g
        for g in report["all_gaps"]
        if g["id"].startswith("rule-") or g["id"] == "intake-date-conflict"
    ]
    for question in (
        priority_gaps
        + report["directions"][-1]["narrative"]["questions"][1:]
        + context.questions
    ):
        if question not in selected and len(selected) < 7:
            selected.append(question)
    report["all_gaps"] += context.questions
    report["gaps"] = selected
    report["narrative_version"] = VERSION
