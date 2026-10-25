"""Recent visible patient actions, scoped to the attending specialist."""

from datetime import datetime, timezone
from app.scales import SCALES


def patients_with_activity(c, doctor_id):
    patients = [
        dict(r)
        for r in c.execute(
            "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name,id",
            (doctor_id,),
        )
    ]
    # Source IDs stay internal; descriptions never expose a private intake draft
    # or copy a patient's free text onto the shared list of patients.
    candidates = c.execute(
        """
        SELECT p.id patient_id, 'diary' kind, d.id object_id,
               coalesce(a.created_at,d.day) happened_at, coalesce(a.id,0) sequence,
               '' detail
        FROM patients p JOIN diary d ON d.patient_id=p.id
        LEFT JOIN audit a ON a.patient_id=p.id AND a.object_id=d.id AND a.action='diary_saved'
        WHERE p.doctor_id=:did
        UNION ALL
        SELECT p.id,'preferences',v.revision,v.created_at,v.revision,'' FROM patients p
        JOIN medication_preference_versions v ON v.patient_id=p.id
        WHERE p.doctor_id=:did AND v.status='sent'
        UNION ALL
        SELECT p.id,'medicine_reaction',r.id,r.created_at,r.id,'' FROM patients p
        JOIN medicine_reaction_reports r ON r.patient_id=p.id
        WHERE p.doctor_id=:did AND r.author_role='patient'
        UNION ALL
        SELECT p.id,'note',n.id,n.created_at,n.id,'' FROM patients p
        JOIN diary_notes n ON n.patient_id=p.id WHERE p.doctor_id=:did
        UNION ALL
        SELECT p.id,'assessment',a.id,a.completed_at,a.id,a.scale FROM patients p
        JOIN assessments a ON a.patient_id=p.id
        WHERE p.doctor_id=:did AND a.status='completed' AND a.completed_at IS NOT NULL
        UNION ALL
        SELECT p.id,'comment',a.id,r.changed_at,r.id,a.scale FROM patients p
        JOIN assessments a ON a.patient_id=p.id JOIN answers r ON r.assessment_id=a.id
        WHERE p.doctor_id=:did AND (trim(r.comment)<>'' OR r.discuss=1)
        UNION ALL
        SELECT p.id,'intake',s.id,s.created_at,s.id,'' FROM patients p
        JOIN intake_submissions s ON s.patient_id=p.id WHERE p.doctor_id=:did
        UNION ALL
        SELECT p.id,'resource',r.id,e.created_at,e.id,r.title FROM patients p
        JOIN resource_assignments r ON r.patient_id=p.id
        JOIN resource_events e ON e.assignment_id=r.id
        JOIN accounts u ON u.id=e.actor_id AND u.role='patient' AND u.patient_id=p.id
        WHERE p.doctor_id=:did AND e.kind='read'
        UNION ALL
        SELECT p.id,'order_result',o.id,f.created_at,f.id,'' FROM patients p
        JOIN care_orders o ON o.patient_id=p.id
        JOIN care_order_files f ON f.order_id=o.id AND f.deleted_at IS NULL
        JOIN accounts u ON u.id=f.owner_id AND u.role='patient' AND u.patient_id=p.id
        WHERE p.doctor_id=:did
    """,
        {"did": doctor_id},
    ).fetchall()
    ordering = {
        (r["action"], r["patient_id"], r["object_id"], r["created_at"]): r["id"]
        for r in c.execute(
            """SELECT a.* FROM audit a
            JOIN patients p ON p.id=a.patient_id AND p.doctor_id=?
            JOIN accounts u ON u.id=a.account_id AND u.role='patient' AND u.patient_id=p.id
            WHERE a.action IN ('diary_saved','diary_note_saved','assessment_saved','intake_submitted','resource_read','care_order_file_sent','medication_preferences_sent','medicine_reaction_reported')
            ORDER BY a.id""",
            (doctor_id,),
        )
    }
    latest = {}
    for row in candidates:
        action = dict(row)
        try:
            instant = datetime.fromisoformat(action["happened_at"])
            # Legacy diaries only contain their calendar date, not a save time.
            instant = (
                instant.replace(tzinfo=timezone.utc)
                if instant.tzinfo is None
                else instant
            )
            audit_action = {
                "diary": "diary_saved",
                "note": "diary_note_saved",
                "assessment": "assessment_saved",
                "comment": "assessment_saved",
                "intake": "intake_submitted",
                "resource": "resource_read",
                "order_result": "care_order_file_sent",
                "preferences": "medication_preferences_sent",
                "medicine_reaction": "medicine_reaction_reported",
            }[action["kind"]]
            sequence = ordering.get(
                (
                    audit_action,
                    action["patient_id"],
                    action["object_id"],
                    action["happened_at"],
                ),
                0,
            )
            action["sort"] = (instant.timestamp(), sequence, action["sequence"])
        except (ValueError, TypeError):
            continue
        pid = action["patient_id"]
        if pid not in latest or action["sort"] > latest[pid]["sort"]:
            scale = SCALES.get(action["detail"], {}).get("name", action["detail"])
            kind = action["kind"]
            action["label"] = {
                "diary": "Заполнен дневник",
                "note": "Добавлена заметка",
                "assessment": f"Пройдена анкета {scale}",
                "comment": f"Добавлено пояснение к анкете {scale}",
                "intake": "Отправлен первичный анамнез",
                "resource": f"Прочитан материал «{action['detail']}»",
                "order_result": "Прикреплён результат к назначению",
                "preferences": "Отправлены пожелания к лекарствам",
                "medicine_reaction": "Сообщено о возможной реакции на лекарство",
            }[kind]
            action["href"] = (
                f"/patients/{pid}/assignments#order-{action['object_id']}"
                if kind == "order_result"
                else (
                    f"/assessments/{action['object_id']}"
                    if kind in ("assessment", "comment")
                    else (
                        f"/patients/{pid}/diary"
                        if kind in ("diary", "note")
                        else (
                            f"/patients/{pid}/profile"
                            if kind == "intake"
                            else "/doctor/resources#assignments"
                        )
                    )
                )
            )
            if kind == "preferences":
                action["href"] = f"/patients/{pid}/assignments#medicines"
            if kind == "medicine_reaction":
                action["href"] = (
                    f"/patients/{pid}/assignments#reaction-report-{action['object_id']}"
                )
            action["date_only"] = len(action["happened_at"]) == 10
            latest[pid] = action
    for patient in patients:
        patient["activity"] = latest.get(patient["id"])
    patients.sort(
        key=lambda p: p["activity"]["sort"] if p["activity"] else (float("-inf"), 0, 0),
        reverse=True,
    )
    return patients
