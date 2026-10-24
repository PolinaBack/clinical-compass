"""Deterministic PHQ-4 follow-up forms; never fills in a patient's answers."""

from app.scales import VERSION, score
from app.storage import audit


def ensure_followups(c, assessment_id=None, actor_id=None):
    rows = c.execute(
        "SELECT * FROM assessments WHERE scale='phq4' AND status IN ('completed','incomplete')"
        + (" AND id=?" if assessment_id is not None else ""),
        (assessment_id,) if assessment_id is not None else (),
    ).fetchall()
    for source in rows:
        answers = {
            r["item"]: dict(r)
            for r in c.execute(
                "SELECT * FROM answers WHERE assessment_id=?", (source["id"],)
            )
        }
        result = score("phq4", answers)
        # Each two-question subscale has its own completeness. An unanswered
        # second subscale must not suppress a qualifying, submitted first one.
        for scale, subtotal in (("phq9", result["phq2"]), ("gad7", result["gad2"])):
            if subtotal is None or subtotal < 3:
                continue
            inserted = c.execute(
                "INSERT OR IGNORE INTO assessments(session_id,patient_id,scale,version) VALUES(?,?,?,?)",
                (source["session_id"], source["patient_id"], scale, VERSION),
            )
            if inserted.rowcount:
                audit(
                    c,
                    actor_id,
                    "assessment_followup_added",
                    source["patient_id"],
                    inserted.lastrowid,
                )
