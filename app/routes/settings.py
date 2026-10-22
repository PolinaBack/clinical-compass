from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from app.storage import audit
from app.services.account_settings import read_settings, save_settings
from app.services.interface import save_interface
from app.services.consent import (
    MEDIA_PURPOSES,
    MEDIA_FIELDS,
    account_context,
    documents,
    latest_event,
    is_granted,
    record_consent,
)


def register_settings(app, db, account, csrf_check, render, has_consent):
    @app.get("/settings")
    def settings(request: Request):
        user = account(request)
        with db() as c:
            preferences = read_settings(c, user)
            docs = documents(c)
            media_context = (
                account_context(c, user["id"]) if user["role"] == "patient" else None
            )
            media_states = {
                p: (
                    latest_event(c, user["id"], p, media_context)
                    if media_context
                    else None
                )
                for p in MEDIA_PURPOSES
            }
            media_allowed = {
                p: bool(media_context and is_granted(c, user["id"], p, media_context))
                for p in MEDIA_PURPOSES
            }
            history = [
                dict(r)
                for r in c.execute(
                    """SELECT e.*,d.title,d.version,d.body,d.retention
                       FROM consent_events e JOIN consent_documents d ON d.id=e.document_id
                       WHERE e.account_id=? ORDER BY e.id DESC""",
                    (user["id"],),
                )
            ]
            legacy = []
            if user["patient_id"] is not None:
                legacy = [
                    dict(r)
                    for r in c.execute(
                        "SELECT * FROM consents WHERE patient_id=? ORDER BY id DESC",
                        (user["patient_id"],),
                    )
                ]
            access_info = (
                c.execute(
                    """SELECT p.name patient_name,d.name doctor_name
                   FROM patients p JOIN doctors d ON d.id=p.doctor_id WHERE p.id=?""",
                    (user["patient_id"],),
                ).fetchone()
                if user["patient_id"] is not None
                else None
            )
            notification_state = latest_event(c, user["id"], "notifications")
            notification_allowed = is_granted(c, user["id"], "notifications")
            audit(c, user["id"], "settings_view", user["patient_id"])
        response = render(
            request,
            "settings.html",
            settings=preferences,
            account_info=user,
            consent_docs=docs,
            media_purposes=MEDIA_PURPOSES,
            media_fields=MEDIA_FIELDS,
            media_states=media_states,
            media_allowed=media_allowed,
            media_context=media_context,
            consent_history=history,
            participation_history=legacy,
            access_info=access_info,
            notification_state=notification_state,
            notification_allowed=notification_allowed,
            participation=(
                has_consent(user["patient_id"]) if user["role"] == "patient" else True
            ),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/settings/{section}")
    async def settings_save(request: Request, section: str):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        with db() as c:
            if section == "interface":
                c.execute("BEGIN IMMEDIATE")
                save_interface(c, user, form)
            else:
                save_settings(c, user, section, form)
        return RedirectResponse(f"/settings?saved={section}#{section}", status_code=303)

    @app.post("/settings/consents/{purpose}")
    async def consent_save(request: Request, purpose: str):
        user = account(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        context_id = str(form.get("context_id", "")) or None
        document_id = str(form.get("document_id", ""))
        expected_id = str(form.get("expected_id", ""))
        if not document_id.isdigit() or not expected_id.isdigit():
            raise HTTPException(422, "Обновите страницу согласий")
        with db() as c:
            record_consent(
                c,
                user,
                purpose,
                context_id,
                str(form.get("action", "")),
                int(document_id),
                int(expected_id),
                form.get("confirmed") == "true",
            )
        return RedirectResponse("/settings?saved=privacy#privacy", status_code=303)
