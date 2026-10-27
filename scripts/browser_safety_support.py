"""End-to-end support/clinician notice check on an isolated synthetic server."""

import json
from pathlib import Path
from playwright.sync_api import expect


def check_safety_support(browser, doctor, base, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    doctor.on("pageerror", lambda e: errors.append(str(e)))
    doctor.goto(base + "/doctor/messages")
    doctor.locator("details.card > summary").click()
    doctor.get_by_role(
        "button", name="Создать чистого тестового пациента", exact=True
    ).click()
    username = doctor.locator("dd code").nth(0).inner_text()
    password = doctor.locator("dd code").nth(1).inner_text()
    safety_path = doctor.locator('a[href$="/safety"]').last.get_attribute("href")
    doctor.goto(base + "/doctor/messages")
    expect(doctor.locator("[data-safety-message-count]")).to_be_hidden()
    patient_context = browser.new_context(viewport=dict(width=1440, height=1000))
    patient = patient_context.new_page()
    patient.on("pageerror", lambda e: errors.append(str(e)))
    patient.goto(base + "/login/patient")
    patient.locator("[name=username]").fill(username)
    patient.locator("[name=password]").fill(password)
    patient.locator('form[action="/login"] button').click()
    patient.goto(base + "/consent")
    patient.locator("[name=accepted]").check()
    patient.locator("[name=adult]").check()
    patient.get_by_role("button", name="Продолжить", exact=True).click()
    patient.goto(base + safety_path)
    patient.locator('form[action="/safety/start"] button').click()
    for item, value in [(1, 0), (2, 1), (3, 0)]:
        patient.locator(f'[name=answer_{item}][value="{value}"]').check()
        patient.locator("#assessment-form button[name=intent][value=next]").click()
    support = patient.locator("[data-safety-support]")
    expect(support).to_be_hidden()
    patient.locator('[name=answer_4][value="1"]').check()
    expect(support).to_be_visible()
    expect(support.get_by_role("link")).to_have_attribute("href", "/help-now")
    expect(doctor.locator("[data-safety-message-count]")).to_be_hidden()
    patient.locator("#assessment-form button[name=intent][value=next]").click()
    doctor.bring_to_front()
    expect(doctor.locator("[data-safety-message-count]")).to_have_text(
        "1", timeout=15000
    )
    expect(doctor.locator("[data-safety-urgent-banner]")).to_contain_text(
        "как можно скорее"
    )
    expect(doctor.locator("[data-safety-messages]")).to_contain_text("Результаты теста")
    expect(doctor.locator("[data-safety-messages]")).to_contain_text(
        "Тест ещё не завершён"
    )
    doctor.get_by_role("button", name="Подтвердить получение", exact=True).click()
    expect(doctor.locator("[data-safety-message-count]")).to_have_text("1")
    note = doctor.locator("[data-safety-action-form] textarea")
    note.fill("Несохранённый текст о контакте с тестовым пациентом.")
    with doctor.expect_response("**/api/safety-messages", timeout=15000):
        patient.locator('[name=answer_5][value="1"]').check()
        patient.locator("#assessment-form button[name=intent][value=next]").click()
        doctor.bring_to_front()
    expect(doctor.locator("[data-safety-messages]")).to_contain_text(
        "Ответы при появлении срочного сообщения", timeout=15000
    )
    expect(note).to_have_value("Несохранённый текст о контакте с тестовым пациентом.")
    dimensions = []
    for width in [320, 390, 768, 1440]:
        doctor.set_viewport_size(dict(width=width, height=1000))
        assert not doctor.evaluate(
            "document.documentElement.scrollWidth > innerWidth+1"
        )
        dimensions.append(width)
        if width in (390, 1440):
            doctor.screenshot(
                path=str(output / f"messages-{width}.png"), full_page=True
            )
    axe = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    violations = None
    if axe.exists():
        doctor.add_script_tag(path=str(axe))
        violations = doctor.evaluate(
            "async () => (await axe.run({include:[['[data-safety-messages]'],['.safety-message-link']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations"
        )
        assert not violations, violations
    doctor.get_by_role("button", name="Сохранить действия врача", exact=True).click()
    expect(doctor.locator("[data-safety-message-count]")).to_be_hidden()
    expect(doctor.locator("[data-urgent-id]")).to_have_count(0)
    patient.goto(base + "/help-now")
    assert patient.locator('a[href="tel:051"]').count()
    assert patient.locator('a[href="tel:+7495051"]').count()
    assert patient.locator('a[href="tel:112"]').count()
    patient.screenshot(path=str(output / "help-1440.png"), full_page=True)
    assert not errors, errors
    patient_context.close()
    (output / "browser.json").write_text(
        json.dumps(
            dict(
                synthetic_only=True,
                clean_account_created_via_ui=True,
                immediate_unsaved_help=True,
                notice_after_partial_save=True,
                top_header_and_red_message=True,
                results_in_notice=True,
                acknowledgement_stays_pinned=True,
                unsaved_doctor_action_preserved=True,
                action_removes_pending_notice=True,
                phones=True,
                widths=dimensions,
                js_errors=errors,
                axe_violations=violations,
            ),
            indent=2,
        )
        + "\n"
    )
