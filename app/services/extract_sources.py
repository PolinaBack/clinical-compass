"""Complete selectable chart text, using submitted intake rather than private drafts."""

from fastapi import HTTPException
from hashlib import sha256
import json

from app.intake_catalog import CATEGORIES, ENTITY_FIELDS, ENTITY_LABELS
from app.services import intake, diary, care_orders, medicine_reactions, meetings
from app.services.clinical_forms import today
from app.services import extract_narratives as prose
from app.scales import SCALES, score, HCL_CONTEXT
from app.storage import now


def fingerprint(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def collect(c, path, user, pid):
    intake.check_scope(c, user, pid, doctor=True)
    patient = dict(c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone())
    submission = intake.read_submission(c, pid)
    data = (
        submission["data"]
        if submission
        else dict(answers={}, entities=[], attachments=[])
    )
    answers = intake.normalized_answers(data.get("answers", {}))
    prefs = data.get("preferences", {})
    source_catalog = data.get("catalog", {})
    categories = source_catalog.get("categories", CATEGORIES)
    entity_fields = source_catalog.get("entity_fields", ENTITY_FIELDS)
    entity_labels = source_catalog.get("entity_labels", ENTITY_LABELS)
    sections = []

    def add(key, group, title, text, source="", default=False, count=None):
        sections.append(
            dict(
                key=key,
                group=group,
                title=title,
                text=text,
                original=text,
                source=source,
                included=bool(text) and default,
                count=count if count is not None else int(bool(text)),
            )
        )

    for category in categories:
        key = category["key"]
        category_answers = answers.get(key, {})
        identity_keys = (
            {"full_name", "birth_date", "sex"}
            if key == "personal"
            else {"height", "weight"} if key == "measurements" else set()
        )
        lines = prose.fields_text(
            key,
            [f for f in category["fields"] if f["key"] not in identity_keys],
            category_answers,
            prefs,
        )
        for entity in data.get("entities", []):
            if entity["kind"] in category.get("kinds", []):
                values = prose.fields_text(
                    entity["kind"],
                    entity_fields.get(entity["kind"], []),
                    entity["data"],
                    entity=True,
                )
                if values:
                    lines.append(
                        entity_labels.get(entity["kind"], entity["kind"])
                        + ". "
                        + " ".join(values)
                    )
        for item in data.get("attachments", []):
            if item.get("category") == key:
                lines.append(
                    "Пациент приложил документ «"
                    + item["filename"]
                    + "»; указанная дата исследования — "
                    + (item.get("study_date") or "не указана")
                    + (
                        ". Описание пациента: «" + item["comment"] + "»"
                        if item.get("comment")
                        else ""
                    )
                    + "."
                )
        source = (
            ("Первичный анамнез, отправлен " + submission["created_at"])
            if submission
            else "Отправленной врачу версии анамнеза нет"
        )
        add(
            "intake-" + key,
            "Анамнез",
            category["title"],
            " ".join(lines),
            source,
            True,
            len(lines),
        )

    def value(section, key):
        item = answers.get(section, {}).get(key, {})
        return str(item.get("value", "")) if item.get("status") == "answered" else ""

    identity = dict(
        full_name=value("personal", "full_name") or patient["name"],
        birth_date=value("personal", "birth_date"),
        sex=value("personal", "sex"),
        height="" if prefs.get("hide_weight") else value("measurements", "height"),
        weight="" if prefs.get("hide_weight") else value("measurements", "weight"),
    )
    profile = c.execute(
        "SELECT * FROM patient_profiles WHERE patient_id=?", (pid,)
    ).fetchone()
    if profile:
        from app.portal import PROFILE_FIELDS

        for key, title, _ in PROFILE_FIELDS:
            if profile[key].strip():
                add(
                    "profile-" + key,
                    "Дополнительные сведения",
                    title,
                    prose.sentence(
                        "В разделе «"
                        + title
                        + "» пациент сообщает: «"
                        + profile[key]
                        + "»"
                    ),
                    "Сохранённый профиль пациента",
                    False,
                )
    family = []
    for row in c.execute(
        "SELECT * FROM relatives WHERE patient_id=? ORDER BY id", (pid,)
    ):
        details = [
            r["label"]
            for r in c.execute(
                "SELECT label FROM family_conditions WHERE relative_id=? ORDER BY id",
                (row["id"],),
            )
        ]
        family.append(
            prose.sentence(
                "В семейном анамнезе указан родственник «"
                + row["relation"]
                + (" — " + row["name"] if row["name"] else "")
                + "»"
                + (
                    ". Со слов пациента, у родственника отмечены: " + "; ".join(details)
                    if details
                    else "; сведений о заболеваниях не представлено"
                )
            )
        )
    if family:
        add(
            "family-tree",
            "Дополнительные сведения",
            "Семейная родословная",
            " ".join(family),
            "Сохранённое семейное дерево",
            True,
            len(family),
        )
    latest = set()
    for row in c.execute(
        "SELECT a.*,s.period_start,s.period_end FROM assessments a JOIN assessment_sessions s ON s.id=a.session_id WHERE a.patient_id=? ORDER BY a.id DESC",
        (pid,),
    ):
        definition = SCALES.get(row["scale"])
        if not definition:
            continue
        raw = {
            a["item"]: dict(a)
            for a in c.execute(
                "SELECT * FROM answers WHERE assessment_id=? ORDER BY id", (row["id"],)
            )
        }
        lines = [
            (
                "Анкета завершена."
                if row["status"] == "completed"
                else "Анкета не завершена."
            ),
            "Ответы относятся к периоду с "
            + row["period_start"]
            + " по "
            + row["period_end"]
            + ".",
        ]
        try:
            result = score(row["scale"], raw)
            if row["status"] == "completed" and result.get("total") is not None:
                lines.append(
                    "Получен результат "
                    + str(result["total"])
                    + " баллов. Результат опросника не является диагнозом."
                )
        except ValueError:
            lines.append("Результат требует проверки исходных ответов.")
        for index, question in enumerate(definition["items"], 1):
            answer = raw.get(str(index), {})
            if not answer:
                continue
            lines.append(prose.assessment_item(row["scale"], index, question, answer))
        if row["scale"] == "hcl33":
            for key, label, choices in HCL_CONTEXT:
                if "ctx_" + key in raw:
                    lines.append(prose.hcl_context(key, raw["ctx_" + key], choices))
        default = row["status"] == "completed" and row["scale"] not in latest
        if row["status"] == "completed":
            latest.add(row["scale"])
        add(
            "assessment-" + str(row["id"]),
            "Анкеты и тесты",
            definition["name"],
            " ".join(lines),
            "Анкета от " + (row["completed_at"] or row["period_end"]),
            default,
        )
    extras = diary.read_diary_extras(c, pid)
    for row in c.execute(
        "SELECT * FROM diary WHERE patient_id=? ORDER BY day DESC,id DESC", (pid,)
    ):
        text = (
            f"В дневнике пациент оценивает самочувствие в {row['wellbeing']} из 10 и указывает продолжительность сна {row['sleep']} ч"
            + (
                f". Качество сна пациент оценивает в {row['sleep_quality']} из 10"
                if row["sleep_quality"] is not None
                else ""
            )
        )
        text = (
            prose.sentence(text)
            + " "
            + {
                "none": "Отметка выполнения лечения указана как неприменимая.",
                "taken": "Пациент сообщает о выполнении лечения.",
                "missed": "Пациент сообщает о пропуске лечения.",
            }.get(row["adherence"], "Выполнение лечения требует уточнения.")
        )
        if row["note"]:
            text += " " + prose.sentence("Пациент поясняет: «" + row["note"] + "»")
        for item in extras["diary_metric_history"].get(row["id"], []):
            text += (
                "\nПациент указал значение показателя «"
                + item["title"].rstrip(" ?")
                + "» — "
                + item["display"]
                + (" " + item["unit"] if item["unit"] else "")
                + ("; " + item["comment"] if item["comment"] else "")
                + ("; " + item["effect_note"] if item.get("effect_note") else "")
                + (
                    "; пациент хочет обсудить это со специалистом"
                    if item["discuss"]
                    else ""
                )
                + "."
            )
        add(
            "diary-" + str(row["id"]),
            "Дневник",
            "Дневник · " + row["day"],
            text,
            "Самоотчёт пациента",
            False,
        )
    seen = set()
    for row in c.execute(
        "SELECT * FROM medicine_plan_shares WHERE patient_id=? ORDER BY id DESC", (pid,)
    ):
        if row["draft_id"] in seen:
            continue
        seen.add(row["draft_id"])
        plan = medicine_reactions.unpack(c, path, row["payload"])
        lines = []
        for item in plan["items"]:
            name = item.get("reference", {}).get("name") or item["code"]
            lines.append(
                "Врач указал в отправленной схеме препарат «"
                + name
                + "»"
                + (
                    ". Указания по приёму: "
                    if any(
                        item.get(k)
                        for k in (
                            "dose",
                            "frequency",
                            "frequency_other",
                            "food",
                            "food_other",
                            "route",
                            "route_other",
                            "start",
                            "duration",
                            "comment",
                        )
                    )
                    else "."
                )
                + "; ".join(
                    str(item.get(k, ""))
                    for k in (
                        "dose",
                        "frequency",
                        "frequency_other",
                        "food",
                        "food_other",
                        "route",
                        "route_other",
                        "start",
                        "duration",
                        "comment",
                    )
                    if item.get(k)
                )
            )
        add(
            "plan-" + str(row["id"]),
            "Назначения и обследования",
            "Отправленная лекарственная схема",
            "\n".join(prose.sentence(line) for line in lines),
            "Отправлена "
            + row["created_at"]
            + ". Фактический приём требует уточнения.",
            True,
        )
    for order in care_orders.listing(c, path, user, pid):
        text = " ".join(
            prose.sentence(template.format(v=order[key]))
            for key, template in {
                "name": "Врач оформил назначение «{v}»",
                "due_label": "Указанный срок выполнения — {v}",
                "status_label": "Текущий статус назначения — {v}",
                "purpose": "Врач указал цель назначения: {v}",
                "preparation": "Врач указал подготовку: {v}",
                "comment": "Врач поясняет: «{v}»",
            }.items()
            if order.get(key)
        )
        add(
            "order-" + str(order["id"]),
            "Назначения и обследования",
            order["kind_label"],
            text,
            order["created_at"],
            order["status"] != "cancelled",
        )
    for row in c.execute(
        "SELECT o.* FROM aviandr_observations o JOIN accounts a ON a.id=o.actor_id WHERE o.patient_id=? AND a.role='doctor' ORDER BY o.id DESC",
        (pid,),
    ):
        value = medicine_reactions.unpack(c, path, row["payload"])
        text = prose.doctor_fact(row["kind"], value, bool(row["confirmed"]))
        add(
            "fact-" + str(row["id"]),
            "Врачебные сведения",
            (
                "Оценка врача в анализе Авиандра"
                if row["kind"] == "direction_review"
                else "Уточнённое сведение врача"
            ),
            text,
            ("Проверено врачом" if row["confirmed"] else "Требует уточнения")
            + " · "
            + row["observed_on"],
            False,
        )
    for row in c.execute(
        "SELECT r.id,r.created_at,m.context_id FROM meeting_recordings r JOIN meeting_sessions m ON m.id=r.meeting_id WHERE m.patient_id=? AND r.deleted_at IS NULL AND r.expires_at>? ORDER BY r.id DESC",
        (pid, now()),
    ):
        from app.services.consent import context_access

        try:
            context_access(c, row["context_id"], user["id"])
        except HTTPException as error:
            if error.status_code in (403, 404):
                continue
            raise
        summary = meetings.published(c, row["id"])
        if summary:
            value = json.loads(summary["payload"])
            lines = [
                v["text"]
                for key in ("summary", "next_steps")
                for v in value.get(key, [])
                if v.get("included", False)
            ]
            add(
                "meeting-" + str(summary["id"]),
                "Консультации",
                "Проверенные итоги встречи",
                "\n".join(lines),
                "Опубликованные итоги · " + summary["created_at"],
                False,
            )
    packet = dict(
        narrative_version=prose.VERSION,
        identity=identity,
        sections=sections,
        intake_count=len(categories),
        intake_submission_id=submission["id"] if submission else None,
        calculation_data=dict(answers=answers, preferences=prefs),
        patient_name=patient["name"],
    )
    return dict(
        packet,
        signature=fingerprint(packet),
        captured_at=now(),
        as_of=today().isoformat(),
    )
