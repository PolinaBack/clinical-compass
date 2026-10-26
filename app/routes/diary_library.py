"""The clinician's ready-made diary library, with patient-scoped assignment."""

import secrets
from urllib.parse import urlencode

from fastapi import HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from app.services import diary_library as library, diary_questions as questions
from app.services.intake import check_scope


def register_diary_library(app, db, account, csrf_check, render):
    def doctor(request):
        user = account(request)
        if user["role"] != "doctor":
            raise HTTPException(403, "Это библиотека специалиста")
        return user

    def patient_id(form):
        try:
            pid = int(form.get("patient_id", ""))
            if not 0 < pid < 2**63:
                raise ValueError()
            return pid
        except (TypeError, ValueError):
            raise HTTPException(
                422, "Выберите пациента для назначения карточки"
            ) from None

    def optional_patient(value):
        return None if value in (None, "") else patient_id({"patient_id": value})

    def location(pid=None, form=None, key=None, added=False):
        data = {"patient_id": pid} if pid else {}
        for name in ("category", "q"):
            if form and form.get(name):
                data[name] = str(form[name])[:200]
        if added:
            data["added"] = "1"
        return (
            "/doctor/diary-cards"
            + ("?" + urlencode(data) if data else "")
            + ("#card-" + key if key else "")
        )

    @app.get("/doctor/diary-cards")
    def ready_cards(
        request: Request,
        patient_id: str | None = Query(None, max_length=20),
        category: str = Query("", max_length=60),
        q: str = Query("", max_length=200),
    ):
        user = doctor(request)
        patient_id = optional_patient(patient_id)
        with db() as c:
            patients = [
                dict(row)
                for row in c.execute(
                    "SELECT id,name FROM patients WHERE doctor_id=? ORDER BY name,id",
                    (user["doctor_id"],),
                )
            ]
            if patient_id:
                check_scope(c, user, patient_id, doctor=True)
            patient = next((p for p in patients if p["id"] == patient_id), None)
            cards = library.cards(c, user)
            assigned = (
                {
                    m.get("template_key"): m
                    for m in questions.definitions(c, patient_id, True)
                }
                if patient_id
                else {}
            )
        needle = q.strip().casefold()
        visible = [
            card
            for card in cards
            if (
                not category
                or category == "own"
                and card["custom"]
                or card["category"] == category
            )
            and (
                not needle
                or needle
                in " ".join(
                    str(card.get(k, "")) for k in ("title", "prompt", "description")
                ).casefold()
            )
        ]
        response = render(
            request,
            "diary_library.html",
            cards=visible,
            total=len(cards),
            categories=library.CATEGORIES,
            category=category,
            q=q,
            patients=patients,
            patient=patient,
            assigned=assigned,
            frequencies=library.FREQUENCIES,
            custom_url="/doctor/diary-cards/new"
            + (f"?patient_id={patient_id}" if patient_id else ""),
            assignment_added=request.query_params.get("added") == "1",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/doctor/diary-cards/new")
    def new_card(request: Request, patient_id: str | None = Query(None, max_length=20)):
        user = doctor(request)
        patient_id = optional_patient(patient_id)
        with db() as c:
            if patient_id:
                check_scope(c, user, patient_id, doctor=True)
        return render(
            request,
            "diary_question_builder.html",
            library_mode=True,
            library_patient_id=patient_id,
            back_url=location(patient_id),
            builder_action="/doctor/diary-cards/new",
            preview_action="/doctor/diary-cards/preview",
            client_token=secrets.token_urlsafe(24),
            question=None,
            patient=None,
            condition_groups=questions.condition_groups(),
            formats=questions.FORMATS,
            modules=questions.MODULES,
        )

    @app.post("/doctor/diary-cards/preview")
    async def preview_card(request: Request):
        doctor(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        return render(
            request,
            "diary_question_preview.html",
            question=questions.configuration(form),
            formats=questions.FORMATS,
            modules=questions.MODULES,
        )

    @app.post("/doctor/diary-cards/new")
    async def save_card(request: Request):
        user = doctor(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        pid = patient_id(form) if form.get("patient_id") else None
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            if pid:
                check_scope(c, user, pid, doctor=True)
            ident = library.save_template(c, user, form)
            if pid:
                library.assign(c, user, pid, f"custom-{ident}", "template")
        return RedirectResponse(
            location(pid, key=f"custom-{ident}", added=bool(pid)), status_code=303
        )

    @app.post("/doctor/diary-cards/{key}/assign")
    async def assign_card(request: Request, key: str):
        user = doctor(request)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        pid = patient_id(form)
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            library.assign(c, user, pid, key, str(form.get("frequency", "template")))
        return RedirectResponse(location(pid, form, key, added=True), status_code=303)
