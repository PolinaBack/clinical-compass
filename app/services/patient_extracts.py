"""Clinician-authored, encrypted, versioned documents with explicit publication."""

from copy import deepcopy
from datetime import date
import json

from fastapi import HTTPException
from app.services import clinical_attachments as files, intake
from app.services.clinical_forms import today
from app.services.extract_sources import collect
from app.storage import audit, now

TITLES = ("Медицинское заключение", "Выписка из медицинской карты", "Справка")
DIAGNOSES = {
    "working": "Рабочая формулировка",
    "preliminary": "Предварительный диагноз",
    "confirmed": "Подтверждённый диагноз",
    "none": "Без нозологического диагноза",
}


def date_label(value):
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value or "Не указана"


def pack(c, path, value):
    return files.cipher(path, c).encrypt(json.dumps(value, ensure_ascii=False).encode())


def unpack(c, path, value):
    try:
        return json.loads(files.cipher(path, c).decrypt(value))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503,
            "Не удалось открыть выписку. Восстановите прежний ключ защищённой резервной копии.",
        ) from None


def row(c, user, pid, eid, *, edit=False):
    intake.check_scope(c, user, pid, doctor=edit)
    item = c.execute(
        "SELECT * FROM patient_extracts WHERE id=? AND patient_id=?", (eid, pid)
    ).fetchone()
    if (
        not item
        or (user["role"] == "patient" and item["published_revision"] is None)
        or (
            user["role"] == "doctor"
            and item["author_id"] != user["id"]
            and item["published_revision"] is None
        )
    ):
        raise HTTPException(404, "Выписка не найдена")
    if edit and item["author_id"] != user["id"]:
        raise HTTPException(403, "Изменять этот документ может его автор")
    return dict(item)


def read(c, path, user, pid, eid, *, edit=False, revision=None):
    item = row(c, user, pid, eid, edit=edit)
    if edit:
        version = item["revision"]
    elif user["role"] == "patient" or item["author_id"] != user["id"]:
        version = item["published_revision"]
        if revision is not None and revision != version:
            raise HTTPException(404, "Эта версия не опубликована")
    else:
        version = revision or item["revision"]
    saved = c.execute(
        "SELECT * FROM patient_extract_versions WHERE extract_id=? AND revision=?",
        (eid, version),
    ).fetchone()
    if not saved:
        raise HTTPException(404, "Версия документа не найдена")
    data = unpack(c, path, saved["payload"])
    if not edit and (user["role"] == "patient" or item["author_id"] != user["id"]):
        data = deepcopy(data)
        data["source"] = {
            "sections": [
                {
                    key: s[key]
                    for key in ("key", "group", "title", "text", "source", "included")
                }
                for s in data["source"]["sections"]
                if s["included"]
            ],
            "captured_at": data["source"]["captured_at"],
        }
    return dict(item, display_revision=version, data=data)


def listing(c, path, user, pid):
    intake.check_scope(c, user, pid)
    result = []
    for item in c.execute(
        "SELECT * FROM patient_extracts WHERE patient_id=? ORDER BY updated_at DESC,id DESC",
        (pid,),
    ):
        if item["published_revision"] is None and (
            user["role"] != "doctor" or item["author_id"] != user["id"]
        ):
            continue
        doc = read(c, path, user, pid, item["id"])
        result.append(doc)
    return result


def create(c, path, user, pid, token):
    intake.check_scope(c, user, pid, doctor=True)
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 100
        or not token.replace("-", "").replace("_", "").isalnum()
    ):
        raise HTTPException(422, "Обновите страницу создания выписки")
    old = c.execute(
        "SELECT id,patient_id FROM patient_extracts WHERE author_id=? AND client_token=?",
        (user["id"], token),
    ).fetchone()
    if old:
        if old["patient_id"] != pid:
            raise HTTPException(409, "Форма создания относится к другой карте")
        return old["id"]
    source = collect(c, path, user, pid)
    doctor = c.execute(
        "SELECT name FROM doctors WHERE id=?", (user["doctor_id"],)
    ).fetchone()
    data = dict(
        title=TITLES[0],
        organization="",
        specialist=doctor["name"] if doctor else "",
        recipient="Другому специалисту",
        date=today().isoformat(),
        identity=deepcopy(source["identity"]),
        source=source,
        diagnosis_code="",
        diagnosis="",
        diagnosis_status="working",
        conclusion="",
        recommendations="",
        additional="",
        reviewed=False,
    )
    ident = c.execute(
        "INSERT INTO patient_extracts(patient_id,author_id,client_token,created_at,updated_at) VALUES(?,?,?,?,?)",
        (pid, user["id"], token, now(), now()),
    ).lastrowid
    c.execute(
        "INSERT INTO patient_extract_versions VALUES(?,1,?,?,?)",
        (ident, pack(c, path, data), user["id"], now()),
    )
    audit(c, user["id"], "patient_extract_created", pid, ident)
    return ident


def candidate(saved, form):
    data = deepcopy(saved)
    for key in (
        "title",
        "organization",
        "specialist",
        "recipient",
        "diagnosis_code",
        "diagnosis",
        "diagnosis_status",
        "conclusion",
        "recommendations",
        "additional",
    ):
        if key in form:
            if len(form.getlist(key)) != 1:
                raise HTTPException(422, "Поле документа передано повторно")
            data[key] = str(form[key])
    for key in data["identity"]:
        if "identity_" + key in form:
            data["identity"][key] = str(form["identity_" + key]).strip()
    allowed = {s["key"] for s in data["source"]["sections"]}
    for key in form:
        for prefix in ("source_", "include_"):
            if key.startswith(prefix) and key[len(prefix) :] not in allowed:
                raise HTTPException(422, "Обновите список сведений этой выписки")
    for section in data["source"]["sections"]:
        key = section["key"]
        if "source_" + key in form:
            section["text"] = str(form["source_" + key])
        section["included"] = form.get("include_" + key) == "yes" and bool(
            section["text"].strip()
        )
    data["reviewed"] = form.get("reviewed") == "yes"
    data["date"] = today().isoformat()
    return data


def calculations(data):
    if "calculation_data" not in data["source"]:
        return deepcopy(data.get("calculations", dict(age=None, bmi=None)))
    source = deepcopy(data["source"]["calculation_data"])
    answers = source.setdefault("answers", {})
    for section, keys in [
        ("personal", ("birth_date", "sex")),
        ("measurements", ("height", "weight")),
    ]:
        group = answers.setdefault(section, {})
        for key in keys:
            value = data["identity"].get(key, "")
            group[key] = (
                dict(status="answered", value=value)
                if value
                else dict(status="unanswered")
            )
    return intake.computations(source, as_of=date.fromisoformat(data["date"]))


def validate(data, *, publish=False):
    if data["title"] not in TITLES or data["diagnosis_status"] not in DIAGNOSES:
        raise HTTPException(422, "Выберите тип документа и статус формулировки врача")
    birth = data["identity"].get("birth_date", "")
    if birth:
        try:
            valid = date.fromisoformat(birth) <= today()
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(
                422, "Проверьте дату рождения: она не может быть позже сегодняшней даты"
            )
    from app.services.diary import number

    for key, label in [("height", "Рост"), ("weight", "Масса")]:
        value = data["identity"].get(key, "").replace(",", ".")
        if value:
            if number(value, label) <= 0:
                raise HTTPException(
                    422,
                    label + ": укажите положительное число или оставьте поле пустым",
                )
            data["identity"][key] = value
            from app.intake_catalog import BY_KEY

            definition = next(
                f for f in BY_KEY["measurements"]["fields"] if f["key"] == key
            )
            if (
                definition.get("minimum") is not None
                and number(value, label) < definition["minimum"]
                or definition.get("maximum") is not None
                and number(value, label) > definition["maximum"]
            ):
                raise HTTPException(
                    422, label + ": значение вне диапазона поля измерений"
                )
    if publish and not data["reviewed"]:
        raise HTTPException(
            422,
            "Перед публикацией или печатью нажмите «Подтвердить заполненные сведения» в форме ниже",
        )
    if publish and not data["identity"]["full_name"].strip():
        raise HTTPException(422, "Укажите ФИО пациента перед публикацией или печатью")
    if publish and not data["specialist"].strip():
        raise HTTPException(
            422, "Укажите ФИО специалиста перед публикацией или печатью"
        )
    return data


def save(c, path, user, pid, eid, form, *, publish=False):
    item = read(c, path, user, pid, eid, edit=True)
    if str(form.get("revision", "")) != str(item["revision"]):
        raise HTTPException(
            409,
            "Документ уже изменён в другом окне. Ваш текст показан ниже; откройте актуальную версию перед сохранением",
        )
    data = validate(candidate(item["data"], form), publish=publish)
    data["date"] = today().isoformat()
    return store_version(c, path, user, pid, eid, item, data, publish=publish)


def store_version(c, path, user, pid, eid, item, data, *, publish=False):
    data["calculations"] = calculations(data)
    version = item["revision"] + 1
    c.execute(
        "INSERT INTO patient_extract_versions VALUES(?,?,?,?,?)",
        (eid, version, pack(c, path, data), user["id"], now()),
    )
    c.execute(
        "UPDATE patient_extracts SET revision=?,updated_at=? WHERE id=?",
        (version, now(), eid),
    )
    if publish:
        c.execute(
            "UPDATE patient_extracts SET published_revision=? WHERE id=?",
            (version, eid),
        )
    audit(
        c,
        user["id"],
        "patient_extract_published" if publish else "patient_extract_saved",
        pid,
        eid,
    )
    return version


def refresh(c, path, user, pid, eid, form):
    item = read(c, path, user, pid, eid, edit=True)
    if str(form.get("revision", "")) != str(item["revision"]):
        raise HTTPException(
            409,
            "Документ изменился. Откройте актуальную версию перед обновлением сведений",
        )
    data = refreshed(c, path, user, pid, candidate(item["data"], form))
    validate(data)
    return store_version(c, path, user, pid, eid, item, data)


def refreshed(c, path, user, pid, data):
    fresh = collect(c, path, user, pid)
    result = deepcopy(data)
    old = {s["key"]: s for s in data["source"]["sections"]}
    for item in fresh["sections"]:
        previous = old.get(item["key"])
        if previous:
            if previous["text"] != previous["original"]:
                item["text"] = previous["text"]
            item["included"] = previous["included"]
    fresh["sections"].extend(
        item
        for key, item in old.items()
        if key not in {s["key"] for s in fresh["sections"]}
    )
    for key, value in fresh["identity"].items():
        if result["identity"][key] == data["source"]["identity"][key]:
            result["identity"][key] = value
    result.update(source=fresh, reviewed=False)
    return result


def withdraw(c, user, pid, eid, form):
    item = row(c, user, pid, eid, edit=True)
    if str(form.get("revision", "")) != str(item["revision"]):
        raise HTTPException(
            409, "Документ изменился. Обновите страницу перед снятием публикации"
        )
    c.execute(
        "UPDATE patient_extracts SET published_revision=NULL,updated_at=? WHERE id=?",
        (now(), eid),
    )
    audit(c, user["id"], "patient_extract_withdrawn", pid, eid)
