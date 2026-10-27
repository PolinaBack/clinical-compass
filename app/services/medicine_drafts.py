"""Doctor-authored educational drafts, never prescriptions or patient orders."""

import json
from datetime import date
from fastapi import HTTPException
from app.storage import audit, now
from app.services.clinical_attachments import cipher
from app.services import drug_library as library
from app.services.medicine_reference import snapshot

FREQUENCIES = (
    "1 раз в день",
    "2 раза в день",
    "3 раза в день",
    "4 раза в день",
    "По необходимости",
    "Другая схема",
)
FOODS = (
    "За 30 минут до еды",
    "До еды",
    "Во время еды",
    "После еды",
    "Независимо от еды",
    "Другое",
)
ROUTES = ("Внутрь", "Другой способ")


def patient(c, user, pid):
    if not isinstance(pid, int) or not 1 <= pid <= 9223372036854775807:
        raise HTTPException(422, "Некорректный номер учебной карты")
    active = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (user["id"],)
    ).fetchone()
    row = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    if (
        not row
        or not active
        or active["auth_version"] != user["auth_version"]
        or active["role"] != "doctor"
        or row["doctor_id"] != active["doctor_id"]
    ):
        raise HTTPException(403 if row else 404, "Нет доступа к этой учебной карте")
    return row


def read(c, path, user, pid, version=None):
    patient(c, user, pid)
    row = c.execute(
        "SELECT * FROM medicine_drafts WHERE patient_id=? AND author_id=?",
        (pid, user["id"]),
    ).fetchone()
    if not row:
        if version is not None:
            raise HTTPException(404, "Версия черновика не найдена")
        return dict(revision=0, items=[], history=[], tolerability={})
    history = [
        dict(r)
        for r in c.execute(
            "SELECT revision,created_at FROM medicine_draft_versions WHERE draft_id=? ORDER BY revision DESC",
            (row["id"],),
        )
    ]
    encrypted = row["payload"]
    if version is not None:
        old = c.execute(
            "SELECT payload FROM medicine_draft_versions WHERE draft_id=? AND revision=?",
            (row["id"], version),
        ).fetchone()
        if not old:
            raise HTTPException(404, "Версия черновика не найдена")
        encrypted = old["payload"]
    key = cipher(path, c)
    try:
        payload = json.loads(key.decrypt(encrypted))
    except Exception:
        raise HTTPException(
            503,
            "Не удалось прочитать черновик. Проверьте прежний ключ защищённых данных.",
        ) from None
    return dict(
        revision=version if version is not None else row["revision"],
        items=payload["items"],
        history=history,
        tolerability=payload.get("tolerability", {}),
    )


def parse(form, drugs):
    codes = form.getlist("drug")
    if (
        not 1 <= len(codes) <= 20
        or len(set(codes)) != len(codes)
        or any(code not in drugs for code in codes)
    ):
        raise HTTPException(
            422, "Выберите хотя бы один препарат из справочника без повторов."
        )
    items = []
    for code in codes:

        def field(name, limit=200):
            values = form.getlist(f"{code}.{name}")
            if len(values) > 1:
                raise HTTPException(422, "Повторяющееся поле схемы")
            value = str(values[0] if values else "").strip()
            if len(value) > limit or any(
                ord(s) < 32 and s not in "\n\r\t" for s in value
            ):
                raise HTTPException(422, "Слишком длинное или некорректное поле схемы")
            return value

        item = {
            key: field(key, 3000 if key == "comment" else 200)
            for key in (
                "dose",
                "frequency",
                "frequency_other",
                "times",
                "food",
                "food_other",
                "route",
                "route_other",
                "start",
                "duration",
                "comment",
            )
        }
        if not item["dose"] or item["frequency"] not in FREQUENCIES:
            raise HTTPException(
                422,
                "Для каждого препарата обязательны доза и кратность. Система не подбирает их автоматически.",
            )
        for key, allowed, other in (
            ("frequency", FREQUENCIES, "Другая схема"),
            ("food", FOODS, "Другое"),
            ("route", ROUTES, "Другой способ"),
        ):
            if item[key] and item[key] not in allowed:
                raise HTTPException(422, "Выберите предусмотренный вариант схемы")
            if item[key] == other:
                if not item[key + "_other"]:
                    raise HTTPException(422, "Уточните выбранный другой вариант схемы")
            else:
                item[key + "_other"] = ""
        if item["start"]:
            try:
                item["start"] = date.fromisoformat(item["start"]).isoformat()
            except ValueError:
                raise HTTPException(422, "Проверьте дату начала") from None
        items.append(dict(code=code, **item, reference=snapshot(drugs[code])))
    return items


def save(c, path, user, pid, form):
    patient(c, user, pid)
    try:
        revision = int(form.get("revision", ""))
        if revision < 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Обновите страницу черновика") from None
    drugs = {d["code"]: d for d in library.search(c)}
    old = read(c, path, user, pid)
    archived = {
        d["code"]: d
        for d in library.search(c, include_archived=True)
        if d.get("archived")
    }
    for item in old["items"]:
        if item["code"] in archived:
            drugs[item["code"]] = archived[item["code"]]
    from app.services.medicine_verification import require

    require(drugs, form.getlist("drug"))
    items = parse(form, drugs)
    old_references = {item["code"]: item["reference"] for item in old["items"]}
    for item in items:
        if item["code"] in archived:
            item["reference"] = old_references[item["code"]]
    from app.services import adverse_reactions

    tolerability = adverse_reactions.preference_snapshot(c, pid, path)
    registry = adverse_reactions.records(c)
    for item in items:
        item["preference_matches"] = adverse_reactions.matches(
            registry, item["code"], tolerability
        )
    if revision != old["revision"]:
        raise HTTPException(
            409,
            "Черновик изменён в другой вкладке. Откройте текущую версию; прежние версии сохранены.",
        )
    if items == old["items"] and tolerability == old.get("tolerability"):
        return
    encrypted = cipher(path, c).encrypt(
        library.dump(dict(items=items, tolerability=tolerability)).encode()
    )
    timestamp = now()
    if old["revision"]:
        row = c.execute(
            "UPDATE medicine_drafts SET payload=?,revision=revision+1,updated_at=? WHERE patient_id=? AND author_id=? RETURNING id",
            (encrypted, timestamp, pid, user["id"]),
        ).fetchone()
        ident = row["id"]
    else:
        ident = c.execute(
            "INSERT INTO medicine_drafts(patient_id,author_id,revision,payload,created_at,updated_at) VALUES(?,?,1,?,?,?)",
            (pid, user["id"], encrypted, timestamp, timestamp),
        ).lastrowid
    c.execute(
        "INSERT INTO medicine_draft_versions VALUES(?,?,?,?)",
        (ident, old["revision"] + 1, encrypted, timestamp),
    )
    audit(c, user["id"], "medicine_draft_saved", pid, ident)
