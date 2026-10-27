"""Version-bound clinician confirmation before a catalogue medicine can be prescribed."""

import hashlib
import json
from datetime import date, timedelta
from fastapi import HTTPException
from app.services import drug_library as library, medicine_reference, doctor_medicines
from app.services.clinical_forms import today
from app.storage import audit

KIND = "prescribing_review"
VERSION = "rf-instruction-v1"
IDENTITY = {
    "name": "Название проверенного препарата",
    "inn": "МНН / действующие вещества",
    "form": "Лекарственная форма",
    "strength": "Дозировка / концентрация формы",
    "manufacturer": "Производитель",
    "registration": "Действующий регистрационный номер РФ",
}
SECTIONS = {
    key: library.FIELDS[key]
    for key in (
        "indications",
        "contraindications",
        "warnings",
        "doses",
        "adverse",
        "interactions",
    )
}


def meaningful(value):
    value = str(value or "").strip()
    return bool(
        value
        and library.normalize(value)
        not in {
            "—",
            "-",
            "лекарство без названия",
            "неизвестно",
            "не знаю",
            "не помню",
            "не проверен",
            "не проверено",
        }
        and not library.normalize(value).startswith(
            ("не указан", "не уточн", "сведения не внесены", "нет данных")
        )
    )


def instruction_text(value):
    return bool(
        str(value or "").strip()
        and not library.normalize(value).startswith("сведения не внесены")
    )


def signature(drug):
    return hashlib.sha256(
        library.dump(
            [
                VERSION,
                {
                    key: drug.get(key)
                    for key in (
                        "code",
                        "_import",
                        "name",
                        "inn",
                        "form",
                        "strength",
                        "manufacturer",
                        "registration",
                        "reference",
                        "source_url",
                        "source_text",
                        "archived",
                    )
                },
            ]
        ).encode()
    ).hexdigest()


def state(drug, review=None):
    stamp = signature(drug)
    reason = (
        "Подтвердите регистрацию в РФ и проверьте необходимые сведения по инструкции."
    )
    allowed = False
    if drug.get("archived"):
        reason = "Карточка удалена из библиотеки. Новое назначение недоступно."
    elif review:
        if review.get("action") == "withdraw":
            reason = "Подтверждение отозвано. Нужна повторная проверка."
        elif review.get("signature") != stamp:
            reason = "Карточка или инструкция изменились после проверки. Проверьте новую версию."
        elif (
            not review.get("checked_on", "")
            <= today().isoformat()
            <= review.get("valid_until", "")
        ):
            reason = (
                "Срок проверки истёк. Подтвердите актуальную регистрацию и инструкцию."
            )
        else:
            allowed = bool(
                review.get("registration_confirmed")
                and review.get("instruction_checked")
                and all(meaningful(review.get("identity", {}).get(k)) for k in IDENTITY)
                and all(
                    instruction_text(review.get("sections", {}).get(k, {}).get("text"))
                    and review["sections"][k].get("locator")
                    for k in SECTIONS
                )
            )
            if allowed:
                reason = "Регистрация в РФ и необходимые сведения инструкции проверены врачом для этой версии карточки."
    return dict(
        allowed=allowed,
        label="Проверен для назначения" if allowed else "Не проверен",
        reason=reason,
        signature=stamp,
        review=review,
    )


def decorate(drug, reviews):
    check = state(drug, reviews.get(drug["code"]))
    value = dict(
        drug,
        prescribing=check,
        can_prescribe=check["allowed"],
        verification_status="verified" if check["allowed"] else "unverified",
    )
    if check["allowed"]:
        value.update(check["review"]["identity"])
    return value


def defaults(drug):
    review = drug["prescribing"].get("review") or {}
    prior = review.get("identity", {})
    sections = {s["key"]: s for s in drug.get("reference", {}).get("sections", [])}
    return dict(
        **{
            key: prior.get(key)
            or (drug.get(key, "") if meaningful(drug.get(key)) else "")
            for key in IDENTITY
        },
        registration_url=review.get("registration_url", ""),
        instruction_url=review.get("instruction_url", ""),
        instruction_version=review.get("instruction_version", ""),
        valid_until=(today() + timedelta(days=90)).isoformat(),
        **{
            "text_" + k: review.get("sections", {}).get(k, {}).get("text")
            or (
                sections.get(k, {}).get("text", "")
                if meaningful(sections.get(k, {}).get("text"))
                else ""
            )
            for k in SECTIONS
        },
        **{
            "locator_" + k: review.get("sections", {}).get(k, {}).get("locator", "")
            for k in SECTIONS
        },
    )


def save(c, user, code, form):
    doctor_medicines.doctor(c, user)
    raw = library.current(c, "drug").get(code)
    if not raw:
        raise HTTPException(404, "Карточка не найдена")
    if raw.get("archived"):
        raise HTTPException(
            422, "Удалённую карточку нельзя подтверждать для назначения"
        )
    drug = medicine_reference.enrich(raw)
    stamp = signature(drug)
    action = str(form.get("action", ""))
    if action not in ("verify", "withdraw"):
        raise HTTPException(422, "Выберите результат проверки")
    if any(len(form.getlist(k)) != 1 for k in form):
        raise HTTPException(422, "Поля проверки не должны повторяться")
    if form.get("signature") != stamp:
        raise HTTPException(
            409,
            "Карточка изменилась. Откройте её актуальную версию перед подтверждением",
        )
    note = str(form.get("note", "")).strip()
    if not 10 <= len(note) <= 3000:
        raise HTTPException(422, "Укажите основание проверки (10–3000 символов)")
    token = str(form.get("client_token", ""))
    if (
        not 20 <= len(token) <= 100
        or not token.replace("-", "").replace("_", "").isalnum()
    ):
        raise HTTPException(422, "Обновите форму проверки")
    data = dict(
        code=code,
        action=action,
        signature=stamp,
        note=note,
        actor_id=user["id"],
        checked_on=today().isoformat(),
        client_token=token,
    )
    if action == "verify":
        if (
            form.get("registration_confirmed") != "yes"
            or form.get("instruction_checked") != "yes"
        ):
            raise HTTPException(
                422,
                "Отдельно подтвердите действующую регистрацию в РФ и проверку инструкции",
            )
        identity = {key: str(form.get(key, "")).strip() for key in IDENTITY}
        if not all(meaningful(v) for v in identity.values()):
            raise HTTPException(
                422,
                "Укажите реквизиты проверенного препарата, конкретную форму и регистрационный номер РФ",
            )
        registration_url = str(form.get("registration_url", "")).strip()
        instruction_url = str(form.get("instruction_url", "")).strip()
        instruction_version = str(form.get("instruction_version", "")).strip()
        if not library.safe_url(registration_url, official=True):
            raise HTTPException(
                422, "Укажите ссылку на конкретную запись регистрации в ГРЛС"
            )
        if not library.safe_url(instruction_url) or not instruction_version:
            raise HTTPException(
                422, "Укажите HTTPS-ссылку и редакцию/дату проверенной инструкции"
            )
        try:
            until = date.fromisoformat(str(form.get("valid_until", "")))
            if not today() <= until <= today() + timedelta(days=366):
                raise ValueError()
        except ValueError:
            raise HTTPException(
                422, "Повторная сверка должна быть от сегодняшнего дня до 366 дней"
            ) from None
        sections = {
            key: dict(
                text=str(form.get("text_" + key, "")).strip(),
                locator=str(form.get("locator_" + key, "")).strip(),
            )
            for key in SECTIONS
        }
        if any(
            not instruction_text(value["text"])
            or not value["locator"]
            or form.get("checked_" + key) != "yes"
            for key, value in sections.items()
        ):
            raise HTTPException(
                422,
                "Проверьте каждый необходимый раздел инструкции: текст, страницу/раздел и отметку сверки",
            )
        data.update(
            identity=identity,
            sections=sections,
            registration_url=registration_url,
            instruction_url=instruction_url,
            instruction_version=instruction_version,
            valid_until=until.isoformat(),
            registration_confirmed=True,
            instruction_checked=True,
        )
    for row in c.execute(
        "SELECT * FROM drug_entities WHERE kind=? AND entity_key=? ORDER BY import_id DESC",
        (KIND, code),
    ):
        prior = json.loads(row["payload"])
        if prior.get("actor_id") == user["id"] and prior.get("client_token") == token:
            if prior != data:
                raise HTTPException(409, "Эта форма уже сохранена с другим результатом")
            return row["import_id"]
    ident = doctor_medicines.write(
        c, user, code, data, library.dump(data).encode(), "new", kind=KIND
    )
    audit(c, user["id"], "medicine_prescribing_" + action, object_id=ident)
    return ident


def require(drugs, codes):
    for code in codes:
        drug = drugs.get(code)
        if not drug or not drug.get("can_prescribe"):
            name = drug.get("name", code) if drug else code
            raise HTTPException(
                422,
                f"{name}: препарат не проверен. Назначение доступно после подтверждения регистрации в РФ и проверки инструкции",
            )


def require_current_items(c, items):
    drugs = {d["code"]: d for d in library.search(c)}
    require(drugs, [i["code"] for i in items])
    for item in items:
        if (
            item.get("reference", {}).get("verification_import")
            != drugs[item["code"]]["prescribing"]["review"]["_import"]
        ):
            raise HTTPException(
                409,
                "Проверка препарата изменилась. Пересохраните схему с актуальными проверенными сведениями перед отправкой",
            )
