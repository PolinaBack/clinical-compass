"""Human-reviewed executable predicates bound to active official source fragments.

No medical rule is preinstalled or inferred from editorial prose or a language model.
Existing source review, expiry, withdrawal and supersession remain authoritative.
"""

import json
import math
import re
from pathlib import Path
from fastapi import HTTPException
from app.storage import audit, now
from app.experimental_ai.scenarios import canonical, digest
from app.services import drug_library as library

KINDS = {
    "indication": "Показания",
    "contraindication": "Противопоказания",
    "caution": "Осторожность",
    "adverse": "Нежелательная реакция",
}
FIELDS = {
    "indication": "indications",
    "contraindication": "contraindications",
    "caution": "warnings",
    "adverse": "adverse",
}
FACTS = {
    "diagnosis": "Диагноз, записанный врачом",
    "factor": "Сопутствующее заболевание или фактор",
    "allergy": "Аллергия / непереносимость",
    "age": "Возраст",
    "reaction": "Сообщённое проявление",
}
OPERATORS = {"equals": "Равно", "lt": "Меньше", "gte": "Не меньше"}


def registration():
    path = (
        Path(__file__).resolve().parents[1]
        / "references"
        / "aviandr_registration_excerpt.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["sha256"] = digest(value)
    return value


def active_sources(c):
    def ru(value):
        return re.sub(r"[\s№N\-]", "", value.upper())

    expected = ru(registration()["registration"])
    return [
        s
        for s in library.sources(c, "D01")
        if ru(s["meta"]["registration"]) == expected
    ]


def add_rule(c, user, values):
    if user["role"] != "doctor":
        raise HTTPException(403, "Правило подтверждает специалист")
    try:
        iid = int(values["import_id"])
        kind, fact, op = values["kind"], values["fact"], values["operator"]
        key = str(values.get("key", "")).strip()
        expected = str(values["expected"]).strip()
        quote = str(values["quote"]).strip()
        label = str(values["label"]).strip()
        if kind not in KINDS or fact not in FACTS or op not in OPERATORS:
            raise ValueError()
        if (
            not label
            or len(label) > 200
            or not 4 <= len(quote) <= 4000
            or len(key) > 200
            or not expected
            or len(expected) > 200
        ):
            raise ValueError()
        if fact != "age" and (op != "equals" or not key):
            raise ValueError()
        if kind == "indication" and fact != "diagnosis":
            raise ValueError()
        if (kind == "adverse" and fact != "reaction") or (
            fact != "age" and expected not in {"present", "absent"}
        ):
            raise ValueError()
        if fact == "age":
            expected = float(expected)
            if not math.isfinite(expected) or not 0 <= expected <= 130:
                raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            422, "Проверьте правило: вид, факт, точный код, условие, значение и цитату."
        ) from None
    source = next((s for s in active_sources(c) if s["id"] == iid), None)
    fragment = (
        next(
            (
                f
                for f in source["meta"]["facts"]
                if f["field"] == FIELDS[kind] and quote in f["text"]
            ),
            None,
        )
        if source
        else None
    )
    if not source or not fragment:
        raise HTTPException(
            422,
            "Нужна действующая проверенная инструкция D01 и дословная цитата из соответствующего раздела. Редакционный справочник не подходит.",
        )
    rule = dict(
        kind=kind,
        fact=fact,
        key=key if fact != "age" else "age",
        operator=op,
        expected=expected,
        label=label,
        quote=quote,
        field=FIELDS[kind],
        locator=fragment["locator"],
        source_sha256=source["sha256"],
    )
    checksum = digest(rule)
    c.execute(
        "INSERT OR IGNORE INTO aviandr_rules(import_id,payload,checksum,actor_id,created_at) VALUES(?,?,?,?,?)",
        (iid, canonical(rule), checksum, user["id"], now()),
    )
    ident = c.execute(
        "SELECT id FROM aviandr_rules WHERE import_id=? AND checksum=?", (iid, checksum)
    ).fetchone()[0]
    audit(c, user["id"], "aviandr_rule_reviewed", object_id=ident)
    return ident


def rules(c):
    sources = {s["id"]: s for s in active_sources(c)}
    result = []
    for row in c.execute("SELECT * FROM aviandr_rules ORDER BY id"):
        source = sources.get(row["import_id"])
        if not source:
            continue
        value = json.loads(row["payload"])
        if (
            digest(value) != row["checksum"]
            or value["source_sha256"] != source["sha256"]
        ):
            continue
        if not any(
            f["field"] == value["field"]
            and value["quote"] in f["text"]
            and f["locator"] == value["locator"]
            for f in source["meta"]["facts"]
        ):
            continue
        result.append(dict(id=row["id"], **value, source=source))
    return result
