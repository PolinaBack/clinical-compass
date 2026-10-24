"""Text-only clinician discussions, independent of patient records and AI."""

import hashlib
import json
import re
import unicodedata

from fastapi import HTTPException

KINDS = {"case": "Клинический случай", "question": "Вопрос коллегам"}
OPERATIONS = {"topic", "edit_topic", "comment", "edit_comment"}

# Conservative, local preflight checks. They detect common direct identifiers,
# not every name or combination of indirect identifiers; the author must review.
PRIVACY_RULES = [
    ("электронную почту", r"[\w.+-]+\s*@\s*[\w.-]+\.[\w-]+"),
    ("ссылку или контакт в мессенджере", r"(?:https?://|www\.|t\.me/|@[\w_]{3,})"),
    ("телефон или длинный номер", r"(?<!\w)\+?\d[\d ()\-–]{8,}\d(?!\w)"),
    (
        "точную календарную дату",
        r"\b(?:\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b",
    ),
    (
        "точную календарную дату",
        r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}\b",
    ),
    (
        "номер документа или медицинской карты",
        r"\b(?:паспорт|снилс|инн|полис|омс|дмс|истори[яи]\s+болезни|медкарт[аы]|карта\s*№)\s*[:№#-]?\s*\d",
    ),
    (
        "адрес",
        r"\b(?:ул(?:ица)?\.?|проспект|переулок|квартир[аы]|адрес|место\s+работы)\s*[:.]?\s+[А-ЯA-Zа-яa-z0-9]",
    ),
    (
        "имя, отчество и фамилию",
        r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\b",
    ),
    ("фамилию с инициалами", r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\."),
    ("инициалы с фамилией", r"\b[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё-]+\b"),
    (
        "имя или фамилию пациента",
        r"\b(?i:пациент(?:ка)?|клиент(?:ка)?)\s+(?!(?:Мужчина|Женщина)\b)[А-ЯЁ][а-яё-]+\b",
    ),
    (
        "имя или фамилию",
        r"\b(?i:фио|фамилия|имя\s+пациента|зовут)\s*[:—-]?\s*[А-ЯЁA-Z][а-яёa-z-]+",
    ),
    (
        "имя с отчеством",
        r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]*(?:ович|евич|овна|евна|ична)\b",
    ),
    (
        "имя и фамилию",
        r"\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]*(?:ова|ева|ина|ский|ская|цкий|цкая|ов|ев|ин)\b",
    ),
    (
        "фамилию и имя",
        r"\b[А-ЯЁ][а-яё-]*(?:ова|ева|ина|ский|ская|цкий|цкая|ов|ев|ин)\s+[А-ЯЁ][а-яё-]+\b",
    ),
    (
        "ссылку на карту пациента",
        r"(?:/patients/|patient[_ -]?id\s*[:=]|id\s+пациента\s*[:=])\s*\d+",
    ),
]


def normalise(value):
    value = unicodedata.normalize("NFKC", value).replace("\r\n", "\n")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in value):
        raise HTTPException(422, "Уберите служебные символы из текста.")
    return value.strip()


def validate_content(kind, title, body, confirmed, *, comment=False):
    title, body = normalise(title), normalise(body)
    if not confirmed:
        raise HTTPException(
            422, "Подтвердите, что убрали сведения, по которым можно узнать человека."
        )
    if kind not in KINDS or not body or len(body) > (2500 if comment else 6000):
        raise HTTPException(
            422, "Выберите тип публикации и заполните текст допустимой длины."
        )
    if not comment and not 5 <= len(title) <= 140:
        raise HTTPException(422, "Заголовок должен содержать от 5 до 140 символов.")
    text = title + "\n" + body
    for label, pattern in PRIVACY_RULES:
        # Personal-name rules are deliberately case-sensitive; other rules are not.
        flags = (
            0
            if label
            in {
                "имя, отчество и фамилию",
                "фамилию с инициалами",
                "инициалы с фамилией",
                "имя с отчеством",
                "имя и фамилию",
                "фамилию и имя",
            }
            else re.IGNORECASE
        )
        if label in {"имя или фамилию пациента", "имя или фамилию"}:
            flags = 0
        if re.search(pattern, text, flags):
            raise HTTPException(
                422,
                f"Перед публикацией уберите {label}. Укажите возрастной диапазон, примерный период и нейтральное описание без имён и контактов.",
            )
    return title, body


def fingerprint(payload, actor):
    data = json.dumps(
        [actor["id"], actor["auth_version"], payload],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(data.encode()).hexdigest()


def topic(c, tid, actor):
    row = c.execute(
        "SELECT * FROM clinician_topics WHERE id=? AND status!='deleted'", (tid,)
    ).fetchone()
    if not row or row["status"] == "hidden" and row["author_id"] != actor["id"]:
        raise HTTPException(404, "Обсуждение недоступно.")
    return dict(row)


def target(c, operation, target_id, actor):
    if operation not in OPERATIONS:
        raise HTTPException(422, "Неизвестное действие.")
    if operation == "topic":
        if target_id:
            raise HTTPException(
                422, "Новая тема не привязывается к карте или другому объекту."
            )
        return None
    if operation in {"edit_topic", "comment"}:
        row = topic(c, target_id, actor)
    else:
        row = c.execute(
            "SELECT * FROM clinician_comments WHERE id=? AND status!='deleted'",
            (target_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Ответ недоступен.")
        row = dict(row)
        topic(c, row["topic_id"], actor)
    if operation.startswith("edit") and row["author_id"] != actor["id"]:
        raise HTTPException(403, "Изменять можно только свои публикации.")
    if operation == "comment" and row["status"] != "published":
        raise HTTPException(409, "Обсуждение скрыто. Сначала исправьте публикацию.")
    return row
