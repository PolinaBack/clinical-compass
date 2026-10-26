"""Scoped requests for patient information; private drafts are never sent."""

import hashlib
import json
from copy import deepcopy
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
        item.update(
            title=TOPICS[item["topic"]][0],
            category=TOPICS[item["topic"]][1],
            kinds=TOPICS[item["topic"]][2],
        )
        result.append(item)
    return result


def states(c, user, pid):
    check_scope(c, user, pid)
    result = {}
    for row in c.execute(
        "SELECT topic,answered_at FROM patient_information_requests WHERE patient_id=? ORDER BY id DESC",
        (pid,),
    ):
        if row["topic"] not in result:
            result[row["topic"]] = "received" if row["answered_at"] else "pending"
    submission = read_submission(c, pid)
    for topic in TOPICS:
        if topic not in result:
            result[topic] = (
                "received"
                if submission and meaningful(portion(submission["data"], topic))
                else "empty"
            )
    return result


def packet(c, user, pid):
    return dict(
        states=states(c, user, pid),
        pending=[
            {key: item[key] for key in ("id", "topic", "category", "title")}
            for item in listing(c, user, pid)
        ],
    )


def meaningful(data):
    facts = list(data["answers"].values()) + [
        a for e in data["entities"] for a in e["data"].values()
    ]
    return any(a.get("status") not in (None, "unanswered") for a in facts)


def send_saved_section(c, user, pid, category, *, kind=None):
    """Explicit section/card saves answer matching requests without sending other drafts."""
    from app.services import intake
    from app.services.clinical_forms import visible
    from app.intake_catalog import (
        CATEGORIES,
        ENTITY_FIELDS,
        ENTITY_LABELS,
        ENTITY_CATEGORY,
        VERSION,
    )

    check_scope(c, user, pid, write=True)
    pending = [
        r
        for r in listing(c, user, pid)
        if r["category"] == category and (kind is None or kind in TOPICS[r["topic"]][2])
    ]
    if not pending:
        return []
    current = intake.snapshot(c, pid)
    raw_answers = current["answers"].get(category, {})
    current["answers"][category] = {
        f["key"]: raw_answers[f["key"]]
        for f in intake.BY_KEY[category]["fields"]
        if f["key"] in raw_answers
        and visible(f, raw_answers, current.get("preferences", {}))
    }
    requested_kinds = {kind for r in pending for kind in TOPICS[r["topic"]][2]}
    for entity in current["entities"]:
        if entity["kind"] in requested_kinds:
            raw = entity["data"]
            entity["data"] = {
                f["key"]: raw[f["key"]]
                for f in intake.entity_fields(c, pid, entity["kind"])
                if f["key"] in raw and visible(f, raw)
            }
    topics = [r["topic"] for r in pending if meaningful(portion(current, r["topic"]))]
    if not topics:
        return []
    latest = read_submission(c, pid)
    published = (
        deepcopy(latest["data"])
        if latest
        else dict(
            answers={},
            entities=[],
            attachments=[],
            links=[],
            preferences={},
        )
    )
    delivered_kinds = set()
    for topic in topics:
        _, group, kinds = TOPICS[topic]
        delivered_kinds.update(kinds)
        part = portion(current, topic)
        fields = [
            f for f in intake.BY_KEY[group]["fields"] if f["key"] in part["answers"]
        ]
        shared = {
            f["key"]: part["answers"][f["key"]]
            for f in fields
            if visible(
                f, current["answers"].get(group, {}), current.get("preferences", {})
            )
        }
        old = published.setdefault("answers", {}).setdefault(group, {})
        keys = (
            ("reactions",)
            if topic == "allergies"
            else (
                ("current", "past", "supplements", "difficulties")
                if topic == "medicines"
                else [f["key"] for f in intake.BY_KEY[group]["fields"]]
            )
        )
        for key in keys:
            old.pop(key, None)
        old.update(shared)
    delivered = []
    for entity in current["entities"]:
        if entity["kind"] not in delivered_kinds:
            continue
        item = deepcopy(entity)
        item["data"] = {
            f["key"]: item["data"][f["key"]]
            for f in intake.entity_fields(c, pid, item["kind"])
            if f["key"] in item["data"] and visible(f, item["data"])
        }
        delivered.append(item)
    published["entities"] = [
        e for e in published.get("entities", []) if e["kind"] not in delivered_kinds
    ] + delivered
    known_ids = {e["id"] for e in published["entities"]}
    delivered_ids = {e["id"] for e in delivered}
    files = {
        f["id"]: f
        for f in published.get("attachments", [])
        if not f.get("entity_id") or f["entity_id"] in known_ids
    }
    files.update(
        {
            f["id"]: f
            for f in current.get("attachments", [])
            if f.get("entity_id") in delivered_ids
        }
    )
    published["attachments"] = list(files.values())
    links = {
        (l["left_id"], l["right_id"]): l
        for l in published.get("links", [])
        if l["left_id"] in known_ids and l["right_id"] in known_ids
    }
    links.update(
        {
            (l["left_id"], l["right_id"]): l
            for l in current.get("links", [])
            if l["left_id"] in known_ids
            and l["right_id"] in known_ids
            and (l["left_id"] in delivered_ids or l["right_id"] in delivered_ids)
        }
    )
    published["links"] = list(links.values())
    published["catalog"] = dict(
        categories=CATEGORIES,
        entity_fields={k: intake.entity_fields(c, pid, k) for k in ENTITY_FIELDS},
        entity_labels=ENTITY_LABELS,
        entity_category=ENTITY_CATEGORY,
    )
    published["requested_topics"] = topics
    if (
        not latest
        or any(
            fingerprint(portion(latest["data"], topic))
            != fingerprint(portion(published, topic))
            for topic in topics
        )
        or any(
            fingerprint(latest["data"].get(key, []))
            != fingerprint(published.get(key, []))
            for key in ("attachments", "links")
        )
    ):
        draft = intake.draft(c, pid)
        if c.execute(
            "SELECT 1 FROM intake_submissions WHERE patient_id=? AND revision=?",
            (pid, draft["revision"]),
        ).fetchone():
            intake.save_draft(
                c,
                pid,
                user,
                draft,
                draft["answers"],
                "request_response_prepared",
                dict(topics=topics),
            )
            draft = intake.draft(c, pid)
        published.update(revision=draft["revision"], catalog_version=VERSION)
        ident = c.execute(
            "INSERT INTO intake_submissions(patient_id,revision,catalog_version,payload,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (
                pid,
                draft["revision"],
                VERSION,
                json.dumps(published, ensure_ascii=False),
                user["id"],
                now(),
            ),
        ).lastrowid
        intake.change(
            c,
            pid,
            user,
            "requested_information_sent",
            draft["revision"],
            dict(submission_id=ident, topics=topics),
            ident,
        )
        # A later full submission at the same unchanged draft must still send
        # the other private sections; reserve a fresh draft revision for it.
        intake.save_draft(
            c,
            pid,
            user,
            draft,
            draft["answers"],
            "request_response_saved",
            dict(submission_id=ident, topics=topics),
        )
    fulfill(c, user, pid, explicit_submission=True, topics=topics)
    return topics


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


def fulfill(c, user, pid, *, explicit_submission=False, topics=None):
    check_scope(c, user, pid, write=True)
    submission = read_submission(c, pid)
    if not submission:
        return
    for request in listing(c, user, pid):
        if topics is not None and request["topic"] not in topics:
            continue
        data = portion(submission["data"], request["topic"])
        if not meaningful(data):
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
