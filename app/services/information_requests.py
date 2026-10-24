"""Scoped requests for patient information; private drafts are never sent."""

import hashlib
import json
from fastapi import HTTPException
from app.services.intake import check_scope, read_submission
from app.storage import now, audit

TOPICS = {
    "medicines": ("Лекарства и фактический приём", "medicines", ("medication",)),
    "allergies": ("Аллергии и непереносимость", "medicines", ("reaction",)),
    "conditions": ("Заболевания и самочувствие", "health", ("condition", "symptom")),
    "goals": ("Пожелания и незавершённые опросники", "goals", ()),
}


def portion(data, topic):
    _, category, kinds = TOPICS[topic]
    answers = data.get("answers", {}).get(category, {})
    if topic in ("medicines", "allergies"):
        keys = (
            ("reactions",)
            if topic == "allergies"
            else ("current", "past", "supplements", "difficulties")
        )
        answers = {k: v for k, v in answers.items() if k in keys}
    return dict(
        answers=answers,
        entities=[
            dict(id=e["id"], version=e["version"], data=e["data"])
            for e in data.get("entities", [])
            if e["kind"] in kinds
        ],
    )


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def listing(c, user, pid):
    check_scope(c, user, pid)
    result = []
    for row in c.execute(
        "SELECT * FROM patient_information_requests WHERE patient_id=? AND answered_at IS NULL ORDER BY id",
        (pid,),
    ):
        item = dict(row)
        item.update(title=TOPICS[item["topic"]][0], category=TOPICS[item["topic"]][1])
        result.append(item)
    return result


def request_information(c, user, pid, topic):
    check_scope(c, user, pid, doctor=True)
    if topic not in TOPICS:
        raise HTTPException(422, "Выберите раздел для уточнения")
    submission = read_submission(c, pid)
    baseline = portion(submission["data"] if submission else {}, topic)
    ids = (
        [
            r["id"]
            for r in c.execute(
                "SELECT id FROM assessments WHERE patient_id=? AND status!='completed' ORDER BY id",
                (pid,),
            )
        ]
        if topic == "goals"
        else []
    )
    inserted = c.execute(
        "INSERT OR IGNORE INTO patient_information_requests(patient_id,requested_by,topic,baseline_hash,assessment_ids,created_at) VALUES(?,?,?,?,?,?)",
        (pid, user["id"], topic, fingerprint(baseline), json.dumps(ids), now()),
    )
    if inserted.rowcount:
        audit(c, user["id"], "patient_information_requested", pid, inserted.lastrowid)


def fulfill(c, user, pid, *, explicit_submission=False):
    check_scope(c, user, pid, write=True)
    submission = read_submission(c, pid)
    if not submission:
        return
    for request in listing(c, user, pid):
        data = portion(submission["data"], request["topic"])
        facts = list(data["answers"].values()) + [
            a for e in data["entities"] for a in e["data"].values()
        ]
        meaningful = any(a.get("status") not in (None, "unanswered") for a in facts)
        if not meaningful:
            continue
        # The patient may resend unchanged, still-current information. Opening
        # a page or saving a private draft never acknowledges the request.
        if explicit_submission:
            c.execute(
                "UPDATE patient_information_requests SET submission_id=? WHERE id=?",
                (submission["id"], request["id"]),
            )
        elif request["submission_id"] is None:
            continue
        ids = json.loads(request["assessment_ids"])
        if any(
            not c.execute(
                "SELECT 1 FROM assessments WHERE id=? AND patient_id=? AND status='completed'",
                (aid, pid),
            ).fetchone()
            for aid in ids
        ):
            continue
        c.execute(
            "UPDATE patient_information_requests SET answered_at=?,submission_id=? WHERE id=? AND answered_at IS NULL",
            (now(), submission["id"], request["id"]),
        )
        audit(c, user["id"], "patient_information_answered", pid, request["id"])
