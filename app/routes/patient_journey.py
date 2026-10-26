"""Private current-account progress for an open patient's header."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from app.services import patient_journey, intake


def register_patient_journey(app, db, account):
    @app.get("/api/me/diary-journey")
    def current_journey(request: Request):
        user = account(request)
        if user["role"] != "patient":
            raise HTTPException(403, "Прогресс доступен в кабинете пациента")
        with db() as c:
            intake.check_scope(c, user, user["patient_id"])
            value = patient_journey.progress(c, user["patient_id"])
        return JSONResponse(value, headers={"Cache-Control": "no-store"})
