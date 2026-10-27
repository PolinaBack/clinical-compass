"""Explicitly synthetic preconditions for tests exercising eligible medicine schemes."""

import secrets
from starlette.datastructures import FormData
from app.services import (
    drug_library as library,
    medicine_reference,
    medicine_verification as verification,
)


def review_form(c, code, **changes):
    drug = medicine_reference.enrich(library.current(c, "drug")[code])
    form = dict(
        csrf="direct-unit-csrf",
        action="verify",
        signature=verification.signature(drug),
        client_token=secrets.token_urlsafe(24),
        registration_confirmed="yes",
        instruction_checked="yes",
        registration_url="https://grls.rosminzdrav.ru/synthetic/" + code,
        instruction_url="https://example.org/synthetic-instruction/" + code,
        instruction_version="СИНТЕТИЧЕСКАЯ ФИКСТУРА, не медицинская проверка",
        valid_until=verification.today().isoformat(),
        note="Синтетическое подтверждение только для теста маршрутов.",
    )
    for key in verification.IDENTITY:
        form[key] = (
            drug.get(key)
            if verification.meaningful(drug.get(key))
            else "СИНТЕТИЧЕСКОЕ ЗНАЧЕНИЕ " + key
        )
    for key in verification.SECTIONS:
        form.update(
            {
                "text_" + key: "СИНТЕТИЧЕСКАЯ ФИКСТУРА: не клинические сведения.",
                "locator_" + key: "Тестовый раздел " + key,
                "checked_" + key: "yes",
            }
        )
    form.update(changes)
    return form


def verify(c, user, code):
    drug = next((d for d in library.search(c) if d["code"] == code), None)
    if drug and not drug["can_prescribe"]:
        verification.save(c, user, code, FormData(review_form(c, code)))
