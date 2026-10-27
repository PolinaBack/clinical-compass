"""Version-bound clinician assessments stored in the existing immutable history."""

import secrets
from fastapi import HTTPException
from app.experimental_ai.scenarios import digest
from app.services import aviandr_applicability as policy, medicine_reactions
from app.services.patient_ai_data import scope
from app.services.clinical_forms import today
from app.storage import audit, now


def save(c, path, user, pid, form):
    from app.services.aviandr_analysis import build

    scope(c, user["id"], pid, user["auth_version"])
    key, decision = str(form.get("direction", "")), str(form.get("decision", ""))
    if key not in policy.OPTIONS or decision not in {
        v[0] for v in policy.OPTIONS.get(key, [])
    } | {"revoke"}:
        raise HTTPException(422, "Выберите направление и результат проверки")
    note = str(form.get("note", "")).strip()
    if decision != "revoke" and form.get("review_ack") != "yes":
        raise HTTPException(
            422,
            "Подтвердите проверку текущих сведений и выбранного результата",
        )
    if decision != "revoke" and len(note) > 2000:
        raise HTTPException(422, "Основание проверки — не более 2000 символов")
    if decision == "revoke":
        note = "Врач отменил подтверждение текущих сведений."
    report = build(c, path, user, pid)
    direction = next(d for d in report["directions"] if d["key"] == key)
    snapshot = direction["review"]["snapshot"]
    if form.get("snapshot") != snapshot:
        raise HTTPException(
            409,
            "Сведения карты или библиотеки изменились. Обновите анализ и проверьте текущие данные перед подтверждением",
        )
    if key == "indications" and decision in policy.REGISTERED:
        if policy.age_state(report["data"]["facts"]) != "adult":
            raise HTTPException(
                422,
                "Для этого показания нужна непротиворечивая запись возраста от 18 лет. Сверьте дату рождения или уточнённый возраст врача",
            )
        if decision == "postcovid" and (
            form.get("covid_relation") != "yes" or form.get("mild_moderate") != "yes"
        ):
            raise HTTPException(
                422,
                "Для постковидного показания отдельно подтвердите связь с COVID-19 и лёгкую/среднюю степень указанного состояния",
            )
    if (
        key == "interactions"
        and decision != "revoke"
        and direction["review"]["unknown_drugs"]
        and form.get("unknown_reviewed") != "yes"
    ):
        raise HTTPException(
            422,
            "Отдельно подтвердите собственную оценку неизвестных библиотеке сочетаний; автоматических данных для них недостаточно",
        )
    nonce = str(form.get("client_token", ""))
    if (
        not 20 <= len(nonce) <= 100
        or not nonce.replace("-", "").replace("_", "").isalnum()
    ):
        raise HTTPException(422, "Обновите форму подтверждения")
    payload = dict(
        key=key,
        value=dict(
            snapshot=snapshot,
            decision=decision,
            unknown_reviewed=form.get("unknown_reviewed") == "yes",
            covid_relation=form.get("covid_relation") == "yes",
            mild_moderate=form.get("mild_moderate") == "yes",
        ),
        note=note,
    )
    prior = c.execute(
        "SELECT * FROM aviandr_observations WHERE actor_id=? AND client_token=?",
        (user["id"], nonce),
    ).fetchone()
    if prior:
        if (
            prior["patient_id"] != pid
            or prior["kind"] != "direction_review"
            or medicine_reactions.unpack(c, path, prior["payload"]) != payload
        ):
            raise HTTPException(
                409, "Форма уже сохранена с другим результатом; откройте новую форму"
            )
        return prior["id"]
    ident = c.execute(
        "INSERT INTO aviandr_observations(patient_id,actor_id,kind,fact_key,confirmed,payload,source_note,observed_on,client_token,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            pid,
            user["id"],
            "direction_review",
            digest(["direction_review", key]),
            int(decision != "revoke"),
            medicine_reactions.pack(c, path, payload),
            "Подтверждение врача в анализе ИИ",
            today().isoformat(),
            nonce,
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "aviandr_direction_reviewed", pid, ident)
    return ident
