"""Three-colour clinical review of the current card and the local drug library.

Library matches and a clinician's assessment are labelled separately. Neither
an empty chart nor an unidentifiable medicine is evidence of safety.
"""

import re
from app.experimental_ai.scenarios import digest
from app.services import drug_library, medicine_reference, adverse_reactions

VERSION = "aviandr-colours-v4-semantic"
REGISTERED = {
    "gad": "Генерализованное тревожное расстройство у взрослых от 18 лет",
    "postcovid": "Определённые тревожные состояния со стрессовой реакцией и нарушением адаптации лёгкой/средней степени после COVID-19 у взрослых от 18 лет",
}
OPTIONS = {
    "indications": [
        ("gad", REGISTERED["gad"]),
        ("postcovid", REGISTERED["postcovid"]),
        ("no_match", "Зарегистрированные показания не совпадают"),
    ],
    "contraindications": [
        (
            "complete",
            "Полный анамнез заболеваний и аллергий проверен, других заболеваний не выявлено",
        )
    ],
    "interactions": [
        ("confirmed", "Полный список терапии и допустимость сочетаний проверены мной")
    ],
    "tolerability": [
        (
            "no_effects",
            "Нежелательных эффектов и приоритетов, препятствующих рассмотрению препарата, не отмечено",
        )
    ],
}


def normalized(value):
    return " ".join(drug_library.normalize(value).split())


def answer(data, section, key):
    for row in data["reported"]:
        if row.get("section") == section:
            return row["answers"].get(key, {})
    return {}


def positive(fact):
    return fact["value"].get("state") == "present"


def reference(c):
    drug = next((d for d in drug_library.search(c) if d["code"] == "D01"), None)
    return drug.get("reference", {}) if drug else {}


def inputs(c, report, rules):
    data = report["data"]
    ref = reference(c)
    sections = {s["key"]: s["text"] for s in ref.get("sections", [])}
    facts = data["facts"]
    selected = lambda kinds: [f for f in facts if f["kind"] in kinds]
    submitted = lambda groups: [
        r
        for r in data["reported"]
        if r.get("section") in groups or r.get("kind") in groups
    ]
    therapy = [
        t
        for t in data["therapy"]
        if t["code"] != "D01" and t["state"] in {"current", "prescribed", "unknown"}
    ]
    pair_checks = []
    for item in therapy:
        verified = next(
            (p for p in report["pairs"] if p["drug"]["code"] == item["code"]), {}
        )
        editorial = medicine_reference.pair("D01", item["code"])
        pair_checks.append(
            dict(drug=item, verified=verified.get("known", []), library=editorial)
        )
    registry = [r for r in adverse_reactions.records(c) if r["drug"] == "D01"]
    common = dict(version=VERSION, patient_id=data["patient_id"])
    return {
        "indications": dict(
            **common,
            demographics=data["demographics"],
            facts=selected({"age", "diagnosis"}),
            reported=submitted({"complaints", "mental", "health", "condition"}),
            profile=data["profile"].get("goals", ""),
            library=sections.get("indications", ""),
            rules=[r for r in rules if r["rule"]["kind"] == "indication"],
        ),
        "contraindications": dict(
            **common,
            demographics=data["demographics"],
            facts=selected({"age", "factor", "diagnosis", "allergy"}),
            reported=submitted({"health", "reproductive", "condition", "reaction"}),
            profile=data["profile"].get("health", ""),
            composition=sections.get("composition", ""),
            library=sections.get("contraindications", ""),
            rules=[
                r for r in rules if r["rule"]["kind"] in {"contraindication", "caution"}
            ],
        ),
        "interactions": dict(
            **common,
            therapy=therapy,
            reported=submitted({"medicines", "medication"}),
            pairs=pair_checks,
            library=sections.get("interactions", ""),
        ),
        "tolerability": dict(
            **common,
            reactions=data["reactions"],
            preferences=data["preferences"],
            choices=data["preference_choices"],
            preference_check=adverse_reactions.preference_check(
                registry, "D01", data["preferences"]
            ),
            reported=submitted({"medicines", "reaction"}),
            registry=registry,
            library=ref.get("reactions", []),
        ),
    }


def review_state(data, key, material):
    review = next(
        (r for r in data.get("direction_reviews", []) if r["key"] == key), None
    )
    current = bool(
        review
        and review["confirmed"]
        and review["value"].get("snapshot") == digest(material)
    )
    return dict(
        current=current,
        stale=bool(review and review["confirmed"] and not current),
        record=review,
        decision=review["value"].get("decision") if current else None,
        adult_confirmed=bool(current and review["value"].get("adult_confirmed")),
    )


def age_state(facts):
    values = {
        f["value"].get("value")
        for f in facts
        if f["kind"] == "age" and isinstance(f["value"].get("value"), (int, float))
    }
    if any(v < 18 for v in values) and any(v >= 18 for v in values):
        return "unknown"
    return "minor" if any(v < 18 for v in values) else "adult" if values else "unknown"


def age_missing(facts):
    return not any(
        f["kind"] == "age" and isinstance(f["value"].get("value"), (int, float))
        for f in facts
    )


def reviewed_age(material, review):
    if age_missing(material["facts"]) and review.get("adult_confirmed"):
        return "adult"
    return age_state(material["facts"])


def indication(material, review):
    facts = [f for f in material["facts"] if f["kind"] == "diagnosis" and positive(f)]
    age = reviewed_age(material, review)
    gad = [
        f
        for f in facts
        if normalized(f["key"])
        in {"f41.1", "гтр", "генерализованное тревожное расстройство"}
        or re.match(r"^f41\.1(?:\s|$)", normalized(f["key"]))
    ]
    post = [
        f
        for f in facts
        if normalized(f["key"]).startswith(("f43.0", "f43.2"))
        or "расстройство адаптации" in normalized(f["key"])
    ]
    detail = (
        "Зарегистрированные показания: "
        + "; ".join(REGISTERED.values())
        + ". Любая тревога сама по себе не является зарегистрированным показанием."
    )
    decision = review["decision"]
    if age == "minor" or decision == "no_match":
        return (
            "critical",
            "Показания не совпадают — пропустить",
            "Применимость Авиандра по указанным показаниям не рассматривается: "
            + (
                "возраст меньше 18 лет."
                if age == "minor"
                else "врач подтвердил отсутствие соответствующего показания."
            ),
            detail,
        )
    chosen = REGISTERED.get(decision)
    verified_matches = [r for r in material["rules"] if r["state"] == "confirmed"]
    if not chosen and gad:
        chosen = REGISTERED["gad"]
    if not chosen and verified_matches:
        chosen = "; ".join(r["rule"]["label"] for r in verified_matches)
    if (
        chosen
        and age == "adult"
        and (review["current"] or any(f["confirmed"] for f in gad) or verified_matches)
    ):
        return (
            "favorable",
            "Зарегистрированное показание подтверждено",
            "Подтверждено конкретное показание: " + chosen + ".",
            detail,
        )
    symptom_text = str([material["profile"], material["reported"]]).casefold()
    anxiety_context = any(
        word in symptom_text for word in ("тревог", "тревож", "беспокойств")
    )
    if chosen or post or not facts or anxiety_context:
        return (
            "attention",
            "Нужно подтвердить конкретное показание",
            (
                "В карте есть сведения о показании: "
                + (chosen or REGISTERED["postcovid"])
                + "; необходимо подтвердить диагноз, возраст и условия применения."
                if chosen or post
                else "Недостаточно сведений для сопоставления с зарегистрированными показаниями; жалобы и баллы анкет не устанавливают диагноз."
            ),
            detail,
        )
    return (
        "critical",
        "Показания не совпадают — пропустить",
        "Записанные заболевания не совпадают с зарегистрированными показаниями Авиандра; препарат по этому критерию не рассматривается.",
        detail,
    )


def hypersensitivity(fact, composition):
    """Unspecified sensitivity is a risk to clarify, not a proven drug allergy."""
    if not positive(fact):
        return None
    text = normalized(fact["key"])
    if re.search(
        r"\b(?:нет|без|отрицает|отрицается|не выявлен[аоы]?|не отмечен[аоы]?)\b", text
    ):
        return None
    sensitive = bool(
        re.search(
            r"гиперчувствител\w*|повышенн\w*\s+чувствител\w*|hypersensitiv\w*", text
        )
    )
    allergy = fact["kind"] == "allergy" or bool(re.search(r"аллерги\w*", text))
    if not sensitive and not allergy:
        return None
    drug = re.search(r"\b(?:авиандр\w*|aviandr|маритупирдин\w*|maritupirdine)\b", text)
    components = (
        "лактоз",
        "касторов",
        "крахмал",
        "гипромеллоз",
        "диоксид титан",
        "макрогол",
    )
    component = any(
        term in normalized(composition) and term in text for term in components
    )
    if drug or component:
        return "direct"
    unspecified = re.fullmatch(
        r"(?:гиперчувствительность|повышенная чувствительность|hypersensitivity)[.!\s]*",
        text,
    )
    unknown_target = re.search(
        r"не уточнен\w*|неизвестн\w*|не указан\w*|к компонентам препарата", text
    )
    if sensitive and (unspecified or unknown_target):
        return "unspecified"
    return None


def contraindication(material, review):
    hits = []
    sensitivity_unknown = []
    if age_state(material["facts"]) == "minor":
        hits.append("возраст до 18 лет")
    aliases = {
        "эпилепсия": {"эпилепсия", "epilepsy", "g40", "g41"},
        "органическое поражение головного мозга": {
            "органическое поражение головного мозга",
            "органические поражения головного мозга",
            "organic_brain_damage",
        },
        "СКФ ниже 60 мл/мин": {
            "скф <60 мл/мин",
            "скф ниже 60 мл/мин",
            "egfr_lt60",
            "renal_impairment_lt60",
        },
        "печёночная недостаточность": {
            "печеночная недостаточность",
            "hepatic_failure",
            "k72",
        },
        "беременность": {"беременность", "pregnancy"},
        "грудное вскармливание": {"лактация", "грудное вскармливание", "breastfeeding"},
    }
    if material["library"]:
        reference_terms = {
            "эпилепсия": ("эпилепс",),
            "органическое поражение головного мозга": ("органическ",),
            "СКФ ниже 60 мл/мин": ("скф",),
            "печёночная недостаточность": ("печеночн",),
            "беременность": ("беремен",),
            "грудное вскармливание": ("лактац", "грудн"),
        }
        library_text = normalized(material["library"])
        candidates = list(material["facts"])
        for row in material["reported"]:
            if (
                row.get("section") == "health"
                and row["answers"].get("known_conditions", {}).get("value") == "Да"
            ):
                field = row["answers"].get("description", {})
                if field.get("status") == "answered":
                    candidates.append(
                        dict(
                            kind="factor",
                            key=field["value"],
                            value=dict(state="present"),
                        )
                    )
            if row.get("section") == "reproductive":
                field = row["answers"].get("pregnancy", {})
                if field.get("status") == "answered" and field.get("value") in {
                    "Беременность",
                    "Лактация",
                }:
                    hits.append(field["value"])
        for fact in candidates:
            if not positive(fact):
                continue
            key = normalized(fact["key"])
            sensitivity = (
                hypersensitivity(fact, material.get("composition", ""))
                if "гиперчувствител" in library_text
                else None
            )
            if sensitivity == "direct":
                hits.append(
                    "гиперчувствительность к Авиандру или указанному в библиотеке компоненту"
                )
            elif sensitivity == "unspecified":
                sensitivity_unknown.append(str(fact["key"]))
            if fact["kind"] in {"factor", "diagnosis"}:
                for label, terms in aliases.items():
                    if not any(term in library_text for term in reference_terms[label]):
                        continue
                    if key in terms or any(
                        re.match(r"^[a-z]\d\d$", term) and key.startswith(term + ".")
                        for term in terms
                    ):
                        hits.append(label)
            if fact["kind"] == "allergy" and key in {
                "авиандр",
                "aviandr",
                "маритупирдин",
                "maritupirdine",
            }:
                hits.append("гиперчувствительность к Авиандру/действующему веществу")
    for item in material["rules"]:
        if item["rule"]["kind"] == "contraindication" and item["state"] in {
            "confirmed",
            "reported",
        }:
            hits.append(item["rule"]["label"])
    if hits:
        return (
            "critical",
            "Обнаружено противопоказание",
            "Обнаружено совпадение с противопоказаниями: "
            + "; ".join(dict.fromkeys(hits))
            + ".",
            "Основание: сведения карты и перечень противопоказаний библиотеки; врач проверяет применимость к конкретной форме препарата.",
        )
    if sensitivity_unknown:
        return (
            "critical",
            "Гиперчувствительность — уточните вещество",
            "В карте указана гиперчувствительность без уточнения вещества. Это возможное совпадение с противопоказанием Авиандра, которое нужно проверить до решения о препарате.",
            "Уточните, относится ли реакция к Авиандру, его действующему веществу или компонентам. Общая запись не устанавливает аллергию на этот препарат; подтверждение полноты анамнеза не снимает этот флаг.",
        )
    cautions = [
        r
        for r in material["rules"]
        if r["state"] in {"confirmed", "reported", "conflict"}
    ]
    pregnancy_uncertain = any(
        r.get("section") == "reproductive"
        and r["answers"].get("pregnancy", {}).get("value") == "Возможна беременность"
        for r in material["reported"]
    )
    unresolved = [
        f["key"]
        for f in material["facts"]
        if f["kind"] in {"factor", "diagnosis", "allergy"}
        and f["value"].get("state") not in {"present", "absent"}
    ]
    if (
        review["current"]
        and reviewed_age(material, review) == "adult"
        and material["library"]
        and not cautions
        and not pregnancy_uncertain
        and not unresolved
    ):
        return (
            "favorable",
            "Анамнез проверен врачом",
            "Врач подтвердил полноту анамнеза заболеваний и аллергий; совпадений с перечнем противопоказаний библиотеки не обнаружено.",
            "Подтверждение относится к текущим сведениям и не означает, что любой возможный риск исключён.",
        )
    if review["current"] and unresolved:
        return (
            "attention",
            "Остались неизвестные факторы",
            "Полнота анамнеза отмечена врачом, но в карте остаются факторы с неизвестным состоянием: "
            + ", ".join(unresolved)
            + ".",
            "Уточните наличие или отсутствие этих факторов в «Добавить уточнённое сведение врача», затем подтвердите обновлённые сведения.",
        )
    if review["current"]:
        blockers = []
        if reviewed_age(material, review) != "adult":
            blockers.append(
                "Возраст не указан: подтвердите в этой карточке, что пациенту 18 лет или больше."
                if age_missing(material["facts"])
                else "Записи возраста противоречат друг другу: уточните возраст в карте."
            )
        if not material["library"]:
            blockers.append(
                "В библиотеке отсутствует перечень противопоказаний: восстановите сведения инструкции."
            )
        if cautions:
            blockers.append(
                "Остались факторы осторожности или противоречия: "
                + "; ".join(r["rule"]["label"] for r in cautions)
                + "."
            )
        if pregnancy_uncertain:
            blockers.append(
                "В карте отмечена возможная беременность: уточните эти сведения."
            )
        return (
            "attention",
            "Анамнез подтверждён — остались уточнения",
            "Врач подтвердил полноту анамнеза заболеваний и аллергий. Совпадений с противопоказаниями не обнаружено, но оценка ещё не завершена.",
            " ".join(blockers),
        )
    return (
        "attention",
        "Недостаточно сведений о заболеваниях",
        "Пациент не предоставил достаточно проверенных сведений о заболеваниях, чтобы сделать вывод, что препарат ему противопоказан или что противопоказания исключены.",
        (
            "Есть фактор, требующий осторожности или уточнения; необходимо проверить его до подтверждения полного анамнеза."
            if cautions
            else "Нужны проверка полного списка заболеваний и аллергий врачом и подтверждённый возраст; отсутствие записи не считается отсутствием заболевания."
        ),
    )


def interaction(material, review):
    risks, unknown = [], []
    for item in material["pairs"]:
        levels = [v["level"] for v in item["verified"]]
        if any(v in {"contraindicated", "high", "caution"} for v in levels) or item[
            "library"
        ]["level"] in {"contraindicated", "high", "caution"}:
            risks.append(item["drug"]["name"])
        elif not levels or any(v == "unknown" for v in levels):
            unknown.append(item["drug"]["name"])
    if risks:
        return (
            "critical",
            "Есть сочетания, требующие исключения или оценки",
            "В библиотеке отмечено противопоказанное сочетание, повышенный риск или необходимость оценки для: "
            + ", ".join(risks)
            + ".",
            "Красный статус отражает найденную запись о риске; предупреждение не превращается автоматически в абсолютное противопоказание. Подтверждение врача не скрывает это совпадение.",
        )
    if review["current"]:
        return (
            "favorable",
            "Сочетаемость оценена врачом",
            (
                "Врач подтвердил полный список терапии и свою оценку допустимости сочетаний, включая неизвестные библиотеке препараты: "
                + ", ".join(unknown)
                + "."
                if unknown
                else "Врач подтвердил отсутствие других принимаемых препаратов либо полный список терапии и допустимость сочетаний."
            ),
            (
                "Автоматическая проверка неизвестных препаратов ограничена; зелёный цвет обозначает подтверждённую оценку врача, а не доказанную безопасность неизвестного сочетания."
                if unknown
                else "Совпадений с записями о риске сочетаний в используемой библиотеке не обнаружено; автоматическое назначение не создаётся."
            ),
        )
    return (
        "attention",
        "Нужно подтвердить терапию и сочетания",
        (
            "Указаны неизвестные или не проверенные по библиотеке лекарства: "
            + ", ".join(unknown)
            + "; до оценки врача сочетаемость не подтверждена."
            if unknown
            else "Полнота списка лекарств и отсутствие других принимаемых препаратов пока не подтверждены врачом."
        ),
        "Пустой список препаратов или отсутствие записи о взаимодействии сами по себе не доказывают совместимость.",
    )


def tolerability(material, review):
    terms = {normalized(r["medical"]) for r in material["registry"]} | {
        normalized(r["term"]) for r in material["library"]
    }
    # Reactions to other medicines also inform the personal adverse-effect
    # profile; their cause is never attributed to Aviandr by this comparison.
    reported = material["reactions"]
    matching = [r["label"] for r in reported if normalized(r["medical"]) in terms]
    matching += [
        v["label"]
        for v in material["preference_check"]["effects"]
        if v["state"] == "listed"
    ]
    for row in material["reported"]:
        if row.get("kind") == "reaction":
            for key in ("name", "description"):
                field = row["data"].get(key, {})
                if (
                    field.get("status") == "answered"
                    and normalized(field.get("value")) in terms
                ):
                    matching.append(str(field["value"]))
    if matching:
        return (
            "critical",
            "Совпадение по нежелательным эффектам",
            "Указанные пациентом или врачом нежелательные эффекты совпадают с библиотекой Авиандра: "
            + ", ".join(dict.fromkeys(matching))
            + ".",
            "Необходимо оценить переносимость и личные приоритеты; совпадение не доказывает причинную связь и не всегда является абсолютным противопоказанием.",
        )
    if (
        material["choices"]
        and material["preference_check"]["state"] == "not_found"
        and not reported
        and not any(r.get("kind") == "reaction" for r in material["reported"])
    ):
        return (
            "favorable",
            "Совпадений по нежелательным эффектам нет",
            "Пациент заполнил пожелания по нежелательным эффектам; указанные эффекты в библиотеке Авиандра не перечислены: "
            + ", ".join(v["label"] for v in material["choices"])
            + ".",
            "По этому критерию препарат можно рассмотреть врачу; отсутствие совпадений в библиотеке не гарантирует индивидуальную переносимость.",
        )
    explicit_none = (
        material["preferences"].get("source") == "medication_preferences"
        and not material["choices"]
    )
    negative = any(
        r.get("section") == "medicines"
        and r["answers"].get("reactions", {}).get("status") == "answered"
        and r["answers"]["reactions"].get("value") == "Нет"
        for r in material["reported"]
    )
    uncertainties = bool(
        reported
        or material["choices"]
        or any(r.get("kind") == "reaction" for r in material["reported"])
    )
    if not uncertainties and terms and (explicit_none or negative or review["current"]):
        return (
            "favorable",
            "Нежелательные эффекты не отмечены",
            "В заполненных сведениях или подтверждении врача нежелательные эффекты не отмечены; совпадений с библиотекой Авиандра нет.",
            "По этому критерию препарат можно рассмотреть врачу; отсутствие сообщений не гарантирует переносимость и не заменяет оценку остальных трёх направлений.",
        )
    return (
        "attention",
        "Данные о переносимости нужно уточнить",
        (
            "Есть сообщения или приоритеты, требующие дополнительной оценки, но точного совпадения с библиотекой не обнаружено."
            if uncertainties
            else "Сведения о нежелательных эффектах не заполнены либо недостаточны; отсутствие записей не считается ответом «побочных эффектов нет»."
        ),
        "Уточните сообщения пациента, наблюдения врача и нежелательные эффекты, которых важно избежать.",
    )


CALCULATORS = dict(
    indications=indication,
    contraindications=contraindication,
    interactions=interaction,
    tolerability=tolerability,
)


def current_description(key, material, review=None):
    if key in {"indications", "contraindications"}:
        ages = [
            str(f["value"]["value"]) for f in material["facts"] if f["kind"] == "age"
        ]
        diagnoses = [
            f["key"] for f in material["facts"] if f["kind"] != "age" and positive(f)
        ]
        if (
            not diagnoses
            and key == "indications"
            and review
            and review["current"]
            and review["decision"] in REGISTERED
        ):
            diagnoses = ["Врач подтвердил показание: " + REGISTERED[review["decision"]]]
        return (
            "Возраст: "
            + (
                ", ".join(ages)
                or (
                    "18+ подтверждено врачом; точный возраст не указан"
                    if review and review.get("adult_confirmed")
                    else "не указан"
                )
            )
            + "; записанные заболевания/факторы: "
            + (
                ", ".join(dict.fromkeys(diagnoses))[:500]
                or (
                    "перечень заболеваний и аллергий проверен врачом"
                    if key == "contraindications" and review and review["current"]
                    else "полный перечень не предоставлен"
                )
            )
            + "."
        )
    if key == "interactions":
        return (
            "В анкете и текущих сведениях о терапии: "
            + (
                ", ".join(t["name"] for t in material["therapy"])[:500]
                or "препараты не перечислены"
            )
            + ". Полнота списка оценивается врачом отдельно."
        )
    labels = [r["label"] for r in material["reactions"]] + [
        v["label"] for v in material["choices"]
    ]
    return (
        "Сообщённые проявления и нежелательные эффекты, которых важно избежать: "
        + (
            ", ".join(labels)[:500]
            or "не перечислены; заполненность проверяется отдельно"
        )
        + "."
    )


def apply(c, report, rules, path):
    from app.services import aviandr_matching

    material = inputs(c, report, rules)
    report["comparison_material"] = material
    comparisons = aviandr_matching.load(c, path, report["data"]["patient_id"], material)
    report["source_checksum"] = digest(
        dict(
            version=VERSION,
            data=report["data"],
            material=material,
            sources=report["sources"],
        )
    )
    gaps = []
    for direction in report["directions"]:
        key = direction["key"]
        original = direction["narrative"]
        old_conclusion = original["conclusion"]
        old_basis = original["basis"]
        old_questions = original["questions"]
        comparison = comparisons.get(key)
        review_material = (
            dict(material[key], ai_comparison=comparison)
            if comparison
            else material[key]
        )
        review = review_state(report["data"], key, review_material)
        level, status, summary, detail = CALCULATORS[key](material[key], review)
        level, status, summary, detail = aviandr_matching.evaluate(
            key, material[key], review, (level, status, summary, detail), comparison
        )
        direction.update(level=level, status=status, ai_comparison=comparison)
        if key == "indications":
            context = [
                old_conclusion[0],
                " ".join(
                    old_conclusion[1:3]
                    if not review["current"]
                    else old_conclusion[1:2]
                ),
            ]
        elif key == "contraindications":
            context = (
                [current_description(key, material[key], review)]
                if review["current"]
                else [
                    old_conclusion[0],
                    old_conclusion[1],
                ]
            )
        elif key == "interactions":
            context = [
                old_conclusion[0],
                (
                    old_conclusion[1]
                    if not review["current"]
                    else current_description(key, material[key])
                ),
            ]
        else:
            context = [old_conclusion[0], " ".join(old_conclusion[1:])]
        direction["narrative"]["conclusion"] = [summary, *context, detail]
        direction["narrative"]["unknowns"] = [detail]
        direction["review"] = dict(
            **review,
            snapshot=digest(review_material),
            options=OPTIONS[key],
            age_missing=key in {"indications", "contraindications"}
            and age_missing(material[key]["facts"]),
            unknown_drugs=[
                p["drug"]["name"]
                for p in material[key].get("pairs", [])
                if not p["verified"] and p["library"]["level"] == "unknown"
            ],
        )
        texts = [
            current_description(key, material[key], review),
            (
                "Текущие сведения проверены врачом."
                + (
                    " Основание: " + review["record"]["note"]
                    if review["record"]["note"]
                    else ""
                )
                if review["current"]
                else "Актуального подтверждения полноты сведений врачом нет; учитываются только отправленные пациентом данные и отдельные проверенные записи врача."
            ),
        ]
        if key == "tolerability" and level == "favorable" and not review["current"]:
            texts[1] = (
                "Получен заполненный отрицательный ответ о реакциях или отправлена анкета без отмеченных нежелательных эффектов; сообщений о проявлениях нет."
            )
        basis = []
        for index, description in enumerate(texts):
            fact = dict(
                id=f"P-{key}-current-{index}",
                direction=key,
                kind="patient_data",
                text=description,
                level=level,
                origin="Текущая отправленная карта и подтверждения врача",
                href=f"/patients/{report['data']['patient_id']}/profile",
                values=None,
                patient=[],
                source=None,
            )
            report["evidence"].append(fact)
            direction["findings"].append(fact)
            basis.append({k: fact[k] for k in ("id", "text", "origin", "href")})
        direction["narrative"]["basis"] = basis if review["current"] else old_basis
        question = (
            "Появились ли новые сведения, требующие пересмотра подтверждённой оценки?"
            if level == "favorable"
            else "Какие из указанных пробелов или совпадений требуют проверки до решения о препарате?"
        )
        gap = dict(
            id=f"clinical-review-{key}",
            direction=key,
            question=f"{direction['title']}: {question}",
            reason=summary,
            evidence_ids=[b["id"] for b in basis],
        )
        confirmed_gap = (
            review["current"] and key == "contraindications" and level == "attention"
        )
        if confirmed_gap:
            gap.update(
                question=(
                    "Подтверждено ли, что пациенту 18 лет или больше?"
                    if age_missing(material[key]["facts"])
                    and not review["adult_confirmed"]
                    else "Какие из оставшихся уточнений нужно проверить?"
                ),
                reason=detail,
            )
        if (
            not confirmed_gap
            and not (review["current"] and level == "favorable")
            and old_questions
        ):
            gap = old_questions[0]
        gaps.append(gap)
        direction["narrative"]["questions"] = (
            [gap]
            if confirmed_gap or (review["current"] and level == "favorable")
            else old_questions
        )
    report["gaps"] = gaps
    report["all_gaps"] = gaps + report["all_gaps"]
    report["applicability_version"] = VERSION
    report["skip_candidate"] = report["directions"][0]["level"] == "critical"
    report["library_basis"] = (
        "Локальная библиотека лекарств; отдельные проверенные правила сохраняют своё происхождение. Подтверждения врача не являются проверкой официального источника."
    )
    report["limitations"][-1] = report["library_basis"]
