"""Version-bound reaction reports and explicit sharing of educational schemes."""

import json
import re
import secrets
from datetime import datetime, date
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from app.storage import audit, now
from app.services import clinical_attachments as files, medicine_drafts, care_orders
from app.services.adverse_reactions import records

IMPACTS = {
    "unknown": "Не могу оценить",
    "mild": "Незначительно мешает",
    "moderate": "Заметно мешает",
    "severe": "Сильно мешает",
}
STATES = {
    "ongoing": "Сохраняется",
    "improved": "Стало легче",
    "resolved": "Прошло",
    "unknown": "Не могу оценить",
}
RELATIONS = {
    "not_assessed": "Связь пока не оценена",
    "possible": "Возможная связь с препаратом",
    "unlikely": "Связь маловероятна по оценке врача",
    "doctor_attributed": "Связь с препаратом указана врачом",
}
PUBLIC_FIELDS = (
    "code",
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


def unpack(c, path, payload):
    try:
        return json.loads(files.cipher(path, c).decrypt(payload))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503,
            "Не удалось открыть лекарственную запись прежним ключом. Восстановите защищённую резервную копию.",
        ) from None


def pack(c, path, value):
    return files.cipher(path, c).encrypt(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    )


def integer(form, key):
    try:
        value = int(care_orders.field(form, key, 20))
        if not 0 < value < 2**63:
            raise ValueError()
        return value
    except ValueError:
        raise HTTPException(
            422, "Проверьте номер и версию лекарственной схемы"
        ) from None


def public_item(item):
    result = {key: item.get(key, "") for key in PUBLIC_FIELDS}
    result["reference"] = {
        key: item.get("reference", {}).get(key, "")
        for key in (
            "name",
            "inn",
            "form",
            "strength",
            "manufacturer",
            "status",
            "reference_version",
        )
    }
    return result


def source(c, path, user, pid, draft_id, revision, code):
    care_orders.scope(c, user, pid)
    if not 0 < draft_id < 2**63 or not 0 < revision < 2**63:
        raise HTTPException(422, "Проверьте версию лекарственной схемы")
    draft = c.execute(
        "SELECT * FROM medicine_drafts WHERE id=? AND patient_id=?", (draft_id, pid)
    ).fetchone()
    if not draft:
        raise HTTPException(404, "Лекарственная схема не найдена")
    shared = c.execute(
        "SELECT * FROM medicine_plan_shares WHERE draft_id=? AND draft_revision=?",
        (draft_id, revision),
    ).fetchone()
    if user["role"] == "patient" or draft["author_id"] != user["id"]:
        if not shared:
            raise HTTPException(403, "Эта версия схемы не отправлена пациенту")
        payload = shared["payload"]
    else:
        version = c.execute(
            "SELECT payload FROM medicine_draft_versions WHERE draft_id=? AND revision=?",
            (draft_id, revision),
        ).fetchone()
        if not version:
            raise HTTPException(404, "Сохранённая версия не найдена")
        payload = version["payload"]
    items = unpack(c, path, payload)["items"]
    item = next((i for i in items if i["code"] == code), None)
    if not item:
        raise HTTPException(422, "Препарата нет в этой сохранённой версии схемы")
    return dict(
        draft_id=draft_id,
        revision=revision,
        code=code,
        item=public_item(item),
        shared=bool(shared),
        form_id=f"reaction-{draft_id}-{revision}-{code}",
    )


def share(c, path, user, pid, form):
    medicine_drafts.patient(c, user, pid)
    revision = integer(form, "revision")
    current = medicine_drafts.read(c, path, user, pid)
    if revision != current["revision"] or not current["items"]:
        raise HTTPException(
            409, "Сначала сохраните актуальную схему и обновите страницу"
        )
    draft = c.execute(
        "SELECT id FROM medicine_drafts WHERE patient_id=? AND author_id=?",
        (pid, user["id"]),
    ).fetchone()
    old = c.execute(
        "SELECT id FROM medicine_plan_shares WHERE draft_id=? AND draft_revision=?",
        (draft["id"], revision),
    ).fetchone()
    if old:
        return old["id"]
    payload = pack(c, path, dict(items=[public_item(i) for i in current["items"]]))
    ident = c.execute(
        "INSERT INTO medicine_plan_shares(patient_id,draft_id,draft_revision,author_id,payload,created_at) VALUES(?,?,?,?,?,?)",
        (pid, draft["id"], revision, user["id"], payload, now()),
    ).lastrowid
    audit(c, user["id"], "medicine_plan_shared", pid, ident)
    return ident


def token(form):
    value = care_orders.field(form, "client_token", 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", value):
        raise HTTPException(422, "Обновите форму сообщения о реакции")
    return value


def visibility(form, user, src):
    value = care_orders.field(form, "patient_visible", 10)
    if value not in ("", "yes"):
        raise HTTPException(422, "Проверьте видимость записи")
    if user["role"] == "patient":
        return 1
    if value and not src["shared"]:
        raise HTTPException(
            422, "Сначала отправьте пациенту соответствующую версию схемы"
        )
    return int(value == "yes")


def assessment(form, user):
    relation = care_orders.field(form, "relation", 30) or "not_assessed"
    note = care_orders.field(form, "assessment_note", 2000)
    if user["role"] != "doctor" and (relation != "not_assessed" or note):
        raise HTTPException(403, "Медицинскую оценку добавляет только специалист")
    if relation not in RELATIONS:
        raise HTTPException(422, "Выберите оценку связи из списка")
    if relation != "not_assessed" and not note:
        raise HTTPException(
            422, "Укажите основание врачебной оценки связи с препаратом"
        )
    return dict(relation=relation, assessment_note=note)


def report(c, path, user, pid, form):
    src = source(
        c,
        path,
        user,
        pid,
        integer(form, "draft_id"),
        integer(form, "revision"),
        care_orders.field(form, "code", 50),
    )
    client_token = token(form)
    symptom = care_orders.field(form, "symptom", 200)
    if symptom == "other":
        label = care_orders.field(form, "custom_symptom", 200)
        if not label:
            raise HTTPException(422, "Опишите предполагаемую реакцию")
        medical = label
        system = "Не указана"
    else:
        row = next(
            (r for r in records(c) if r["drug"] == src["code"] and r["key"] == symptom),
            None,
        )
        if not row:
            raise HTTPException(
                422, "Выберите реакцию для этого препарата или свой вариант"
            )
        label = row["label"]
        medical = row["medical"]
        system = row["system"]
    onset = care_orders.field(form, "onset", 20)
    if onset:
        try:
            parsed = date.fromisoformat(onset)
            if parsed > datetime.now(ZoneInfo("Europe/Moscow")).date():
                raise ValueError()
            onset = parsed.isoformat()
        except ValueError:
            raise HTTPException(
                422, "Дата начала реакции не может быть в будущем"
            ) from None
    impact = care_orders.field(form, "impact", 20)
    state = care_orders.field(form, "state", 20)
    if impact not in IMPACTS or state not in STATES:
        raise HTTPException(422, "Выберите выраженность и текущее состояние")
    payload = dict(
        symptom_key=symptom,
        label=label,
        medical=medical,
        system=system,
        onset=onset,
        impact=impact,
        state=state,
        description=care_orders.field(form, "description", 3000),
        medicine=src["item"],
        **assessment(form, user),
    )
    visible = visibility(form, user, src)
    old = c.execute(
        "SELECT * FROM medicine_reaction_reports WHERE author_id=? AND client_token=?",
        (user["id"], client_token),
    ).fetchone()
    if old:
        if (
            old["patient_id"],
            old["draft_id"],
            old["draft_revision"],
            old["drug_code"],
            old["patient_visible"],
        ) != (pid, src["draft_id"], src["revision"], src["code"], visible) or unpack(
            c, path, old["payload"]
        ) != payload:
            raise HTTPException(
                409, "Эта форма уже отправлена. Откройте новую форму реакции."
            )
        return old["id"]
    ident = c.execute(
        "INSERT INTO medicine_reaction_reports(patient_id,draft_id,draft_revision,drug_code,author_id,author_role,patient_visible,payload,client_token,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            pid,
            src["draft_id"],
            src["revision"],
            src["code"],
            user["id"],
            user["role"],
            visible,
            pack(c, path, payload),
            client_token,
            now(),
        ),
    ).lastrowid
    audit(c, user["id"], "medicine_reaction_reported", pid, ident)
    return ident


def report_row(c, user, pid, rid):
    care_orders.scope(c, user, pid)
    row = c.execute(
        "SELECT r.*,d.author_id draft_author FROM medicine_reaction_reports r JOIN medicine_drafts d ON d.id=r.draft_id WHERE r.id=? AND r.patient_id=?",
        (rid, pid),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сообщение о реакции не найдено")
    shared = c.execute(
        "SELECT 1 FROM medicine_plan_shares WHERE draft_id=? AND draft_revision=?",
        (row["draft_id"], row["draft_revision"]),
    ).fetchone()
    if (
        user["role"] == "patient"
        and (not shared or not row["patient_visible"])
        or user["role"] == "doctor"
        and row["draft_author"] != user["id"]
        and not shared
    ):
        raise HTTPException(403, "Эта запись недоступна")
    return row


def review(c, path, user, pid, form):
    care_orders.scope(c, user, pid, doctor=True)
    row = report_row(c, user, pid, integer(form, "report_id"))
    data = assessment(form, user)
    if not data["assessment_note"]:
        raise HTTPException(422, "Добавьте комментарий к оценке")
    visible = visibility(form, user, dict(shared=bool(row["patient_visible"])))
    client_token = token(form)
    old = c.execute(
        "SELECT * FROM medicine_reaction_reviews WHERE author_id=? AND client_token=?",
        (user["id"], client_token),
    ).fetchone()
    if old:
        if (
            old["report_id"] != row["id"]
            or old["patient_visible"] != visible
            or unpack(c, path, old["payload"]) != data
        ):
            raise HTTPException(409, "Эта оценка уже сохранена. Обновите страницу.")
        return old["id"]
    ident = c.execute(
        "INSERT INTO medicine_reaction_reviews(report_id,author_id,patient_visible,payload,client_token,created_at) VALUES(?,?,?,?,?,?)",
        (row["id"], user["id"], visible, pack(c, path, data), client_token, now()),
    ).lastrowid
    audit(c, user["id"], "medicine_reaction_reviewed", pid, row["id"])
    return ident


def listing(c, path, user, pid):
    care_orders.scope(c, user, pid)
    result = []
    rows = c.execute(
        """SELECT r.*,d.author_id draft_author, EXISTS(SELECT 1 FROM medicine_plan_shares s WHERE s.draft_id=r.draft_id AND s.draft_revision=r.draft_revision) shared
        FROM medicine_reaction_reports r JOIN medicine_drafts d ON d.id=r.draft_id WHERE r.patient_id=? ORDER BY r.id DESC""",
        (pid,),
    )
    for row in rows:
        if (
            user["role"] == "patient"
            and (not row["shared"] or not row["patient_visible"])
            or user["role"] == "doctor"
            and row["draft_author"] != user["id"]
            and not row["shared"]
        ):
            continue
        item = dict(
            id=row["id"],
            draft_id=row["draft_id"],
            revision=row["draft_revision"],
            code=row["drug_code"],
            author_role=row["author_role"],
            patient_visible=bool(row["patient_visible"]),
            created_at=row["created_at"],
            **unpack(c, path, row["payload"]),
        )
        item["reviews"] = []
        for r in c.execute(
            "SELECT * FROM medicine_reaction_reviews WHERE report_id=? ORDER BY id DESC",
            (row["id"],),
        ):
            if user["role"] == "patient" and not r["patient_visible"]:
                continue
            item["reviews"].append(
                dict(
                    id=r["id"],
                    created_at=r["created_at"],
                    patient_visible=bool(r["patient_visible"]),
                    **unpack(c, path, r["payload"]),
                )
            )
        result.append(item)
    return result


def common_context(c, path, user, pid):
    return dict(
        reaction_reports=listing(c, path, user, pid),
        reaction_impacts=IMPACTS,
        reaction_states=STATES,
        reaction_relations=RELATIONS,
        reaction_registry=records(c),
        reaction_token=lambda: secrets.token_urlsafe(24),
        reaction_today=datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat(),
    )


def doctor_context(c, path, user, pid, current):
    care_orders.scope(c, user, pid, doctor=True)
    data = common_context(c, path, user, pid)
    draft = c.execute(
        "SELECT id FROM medicine_drafts WHERE patient_id=? AND author_id=?",
        (pid, user["id"]),
    ).fetchone()
    sources = {}
    if draft and current["revision"]:
        for item in current["items"]:
            sources[item["code"]] = source(
                c, path, user, pid, draft["id"], current["revision"], item["code"]
            )
    data["reaction_sources"] = sources
    data["reaction_draft_id"] = draft["id"] if draft else None
    data["reaction_shared"] = bool(
        draft
        and c.execute(
            "SELECT 1 FROM medicine_plan_shares WHERE draft_id=? AND draft_revision=?",
            (draft["id"], current["revision"]),
        ).fetchone()
    )
    return data


def patient_context(c, path, user, pid):
    care_orders.scope(c, user, pid)
    if user["role"] != "patient":
        raise HTTPException(403, "Это раздел пациента")
    data = common_context(c, path, user, pid)
    plans = []
    for row in c.execute(
        "SELECT * FROM medicine_plan_shares WHERE patient_id=? ORDER BY id DESC", (pid,)
    ):
        value = unpack(c, path, row["payload"])
        items = []
        for item in value["items"]:
            src = source(
                c, path, user, pid, row["draft_id"], row["draft_revision"], item["code"]
            )
            src["reports"] = [
                r
                for r in data["reaction_reports"]
                if (r["draft_id"], r["revision"], r["code"])
                == (src["draft_id"], src["revision"], src["code"])
            ]
            src["earlier_reports"] = [
                r
                for r in data["reaction_reports"]
                if r["code"] == src["code"]
                and (r["draft_id"], r["revision"]) != (src["draft_id"], src["revision"])
            ]
            items.append(src)
        plans.append(
            dict(
                id=row["id"],
                created_at=row["created_at"],
                revision=row["draft_revision"],
                items=items,
            )
        )
    data["shared_medicine_plans"] = plans
    from app.services.aviandr_data import observations

    data["medicine_intake_events"] = [
        o for o in observations(c, path, pid) if o["kind"] == "intake_event"
    ]
    return data
