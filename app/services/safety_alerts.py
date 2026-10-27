"""Persistent, assigned-clinician-only contact notices with original evidence."""

from app.safety import SAFETY_ITEMS, safety_urgent_items, normalize_safety
from app.services.medicine_reactions import pack, unpack
from app.storage import now, audit

KIND = "safety6_urgent_"
REASONS = {
    "4": "Указано намерение действовать",
    "5": "Указаны план и намерение",
    "7": "Указано поведение за последние три месяца",
}
STATES = {
    "unanswered": "Нет ответа",
    "deferred": "Отложено",
    "declined": "Пациент не готов отвечать",
    "not_asked_by_rule": "Не задавался по правилу",
}


def answer_rows(answers):
    normalized = normalize_safety(answers)
    return [
        dict(
            item=str(i),
            label="6а" if i == 7 else str(i),
            question=question,
            answer=(
                ("Да" if a.get("value") == 1 else "Нет")
                if a.get("state") == "answered" and a.get("value") in (0, 1)
                else STATES.get(a.get("state"), "Нет ответа")
            ),
        )
        for i, question in enumerate(SAFETY_ITEMS, 1)
        for a in [normalized.get(str(i), {})]
    ]


def record(c, path, assessment, previous, answers, revision, actor_id):
    triggers = safety_urgent_items(answers)
    if not triggers:
        return None
    if (
        not set(triggers) - set(safety_urgent_items(previous))
        and c.execute(
            "SELECT 1 FROM safety_urgent_snapshots u JOIN assessment_events e ON e.id=u.event_id WHERE e.assessment_id=? LIMIT 1",
            (assessment["id"],),
        ).fetchone()
    ):
        return None
    active = c.execute(
        "SELECT id FROM assessment_events WHERE assessment_id=? AND kind LIKE 'safety6_urgent_%' AND action_at IS NULL ORDER BY id DESC LIMIT 1",
        (assessment["id"],),
    ).fetchone()
    if active:
        return active["id"]
    event_id = c.execute(
        "INSERT INTO assessment_events(patient_id,assessment_id,kind,created_at) VALUES(?,?,?,?)",
        (assessment["patient_id"], assessment["id"], KIND + str(revision), now()),
    ).lastrowid
    c.execute(
        "INSERT INTO safety_urgent_snapshots VALUES(?,?)",
        (
            event_id,
            pack(
                c,
                path,
                dict(
                    version=1,
                    triggers=triggers,
                    answers=answer_rows(answers),
                    revision=revision,
                    scale_version=assessment["version"],
                ),
            ),
        ),
    )
    audit(
        c, actor_id, "safety_urgent_notice_created", assessment["patient_id"], event_id
    )
    return event_id


def pending(c, path, doctor_id):
    rows = [
        dict(r)
        for r in c.execute(
            """SELECT e.*,p.name,a.status,a.version,s.period_start,s.period_end,u.payload
        FROM safety_urgent_snapshots u JOIN assessment_events e ON e.id=u.event_id
        JOIN patients p ON p.id=e.patient_id JOIN assessments a ON a.id=e.assessment_id
        JOIN assessment_sessions s ON s.id=a.session_id
        WHERE p.doctor_id=? AND e.action_at IS NULL ORDER BY e.id DESC""",
            (doctor_id,),
        )
    ]
    for row in rows:
        original = unpack(c, path, row.pop("payload"))
        current = {
            a["item"]: dict(a)
            for a in c.execute(
                "SELECT item,value,state FROM answers WHERE assessment_id=?",
                (row["assessment_id"],),
            )
        }
        row.update(
            reasons=[REASONS[i] for i in original["triggers"]],
            original_answers=original["answers"],
            answers=answer_rows(current),
            triggers=original["triggers"],
        )
        row["changed_since_signal"] = row["answers"] != row["original_answers"]
    return rows
