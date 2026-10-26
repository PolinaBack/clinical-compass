"""Doctor-authored catalogue entries; extraction never verifies clinical facts."""

import hashlib
from app.services import drug_library as library
from app.services.medicine_web import SECTIONS, checked_url
from app.services.clinical_forms import today
from app.storage import now, audit
from fastapi import HTTPException

METADATA = {
    "name": "Название препарата",
    "inn": "МНН / действующее вещество",
    "group": "Группа",
    "form": "Лекарственная форма",
    "strength": "Дозировка формы",
    "manufacturer": "Производитель",
    "package": "Количество в упаковке",
    "registration": "Регистрационный номер",
    "atc": "Код АТХ",
    "aliases": "Другие названия",
}


def values(form):
    allowed = {
        "csrf",
        "client_token",
        "source_url",
        *METADATA,
        *("section_" + k for k in SECTIONS),
    }
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form):
        raise HTTPException(
            422, "Проверьте поля карточки; повторяющиеся значения не принимаются"
        )
    result = {}
    for key in [*METADATA, *("section_" + k for k in SECTIONS), "source_url"]:
        value = form.get(key, "")
        if not isinstance(value, str) or len(value) > (
            15000
            if key.startswith("section_")
            else 2000 if key == "source_url" else 500
        ):
            raise HTTPException(
                422, "Слишком длинное или неверное значение поля карточки"
            )
        result[key] = value.strip()
    result["name"] = result["name"] or "Лекарство без названия"
    if result["source_url"]:
        checked_url(result["source_url"])
    return result


def doctor(c, user):
    active = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (user["id"],)
    ).fetchone()
    if (
        not active
        or active["role"] != "doctor"
        or active["auth_version"] != user["auth_version"]
    ):
        raise HTTPException(403, "Лекарство добавляет специалист")


def save(c, user, form):
    doctor(c, user)
    data = values(form)
    token = form.get("client_token", "")
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise HTTPException(422, "Обновите форму добавления лекарства")
    code = (
        "U" + hashlib.sha256(f"{user['id']}:{token}".encode()).hexdigest()[:12].upper()
    )
    content = library.dump(data).encode("utf-8")
    checksum = hashlib.sha256(content).hexdigest()
    existing = library.current(c, "drug")
    if code in existing:
        prior = c.execute(
            "SELECT sha256 FROM drug_imports WHERE id=?", (existing[code]["_import"],)
        ).fetchone()
        if prior["sha256"] != checksum:
            raise HTTPException(
                409, "Эта форма уже сохранена с другими данными. Откройте новую форму"
            )
        return code
    reference = dict(
        sections=[
            dict(
                key=k,
                title=title,
                text=data["section_" + k]
                or "Сведения не внесены. Уточните по инструкции конкретной формы.",
            )
            for k, title in SECTIONS.items()
        ],
        reactions=[],
        links=[data["source_url"]] if data["source_url"] else [],
        reference_date=today().isoformat(),
        revision="doctor-card-v1",
        discrepancy="Карточка добавлена специалистом. Содержание не становится проверенной официальной инструкцией автоматически.",
    )
    drug = {k: data[k] for k in METADATA}
    drug.update(
        code=code,
        substance=data["inn"],
        created_by=user["id"],
        source_url=data["source_url"],
        reference_override=reference,
    )
    ident = write(c, user, code, drug, content, "new")
    audit(c, user["id"], "doctor_medicine_added", object_id=ident)
    return code


def write(c, user, code, drug, content, status):
    payload = dict(entities=[["drug", code, drug]], sheets={})
    change = dict(kind="drug", key=code, status=status, before=None, after=drug)
    base = c.execute(
        "SELECT coalesce(max(id),0) FROM drug_imports WHERE committed_at IS NOT NULL"
    ).fetchone()[0]
    report = dict(
        errors=[], warnings=[], changes=[change], base=base, duplicate_import=None
    )
    ident = c.execute(
        "INSERT INTO drug_imports(kind,filename,sha256,content,payload,report,actor_id,created_at) VALUES('library',?,?,?,?,?,?,?)",
        (
            f"doctor-card-{code}.json",
            hashlib.sha256(content).hexdigest(),
            content,
            library.dump(payload),
            library.dump(report),
            user["id"],
            now(),
        ),
    ).lastrowid
    library.commit(c, ident)
    return ident


def remove(c, user, code):
    doctor(c, user)
    drug = library.current(c, "drug").get(code)
    if not drug:
        raise HTTPException(404, "Карточка не найдена")
    if not drug.get("reference_override") or drug.get("created_by") != user["id"]:
        raise HTTPException(403, "Удалить добавленную карточку может её автор")
    if drug.get("archived"):
        return
    drug.pop("_import", None)
    drug.update(archived=True, archived_by=user["id"], archived_at=now())
    ident = write(c, user, code, drug, library.dump(drug).encode("utf-8"), "changed")
    audit(c, user["id"], "doctor_medicine_removed", object_id=ident)
