"""Questionnaire UI acceptance on a caller-owned, disposable synthetic database.

Supply two authenticated Playwright pages; never run against patient records.
The caller grants the synthetic patient's existing research consent.
"""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_questionnaires(patient, doctor, url):
    out = Path("artifacts/questionnaires")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.emulate_media(reduced_motion="reduce")

    def choose(item, value):
        patient.locator(f'[name="answer_{item}"][value="{value}"]').check()

    def save(label):
        patient.get_by_role("button", name=label, exact=True).click()

    def assign(name):
        doctor.goto(url + "/clinical-tools?patient_id=1")
        expect(doctor.locator(".tool-tile")).to_have_count(9)
        expect(doctor.locator("[data-questionnaire-assignment][open]")).to_have_count(0)
        doctor.get_by_role("button", name="Назначить " + name, exact=True).click()

    def open_assessment(name):
        patient.goto(url + "/patients/1/profile/assessments")
        patient.locator(".assessment-row").filter(has_text=name).first.get_by_role(
            "link"
        ).click()
        return patient.url.split("?")[0]

    patient.goto(url + "/patients/1/profile/assessments")
    expect(patient.locator(".initial-assessment")).to_have_count(2)
    patient.get_by_role("button", name="Начать C-SSRS", exact=False).click()
    safety_url = patient.url.split("?")[0]
    for item in (1, 2):
        expect(patient.locator(".question-card")).to_have_count(1)
        choose(item, 0)
        save("Сохранить и продолжить")
    expect(patient.locator('[name="step_item"]')).to_have_value("6")
    choose(6, 1)
    expect(patient.get_by_role("button", name="Сохранить и продолжить")).to_be_visible()
    save("Сохранить и продолжить")
    expect(patient.locator('[name="step_item"]')).to_have_value("7")
    choose(7, 0)
    save("Завершить и отправить")
    expect(patient.locator("main")).to_contain_text(
        "Это не заключение об отсутствии риска."
    )
    patient.get_by_role("button", name="Продолжить с PHQ-4", exact=True).click()
    phq4_url = patient.url.split("?")[0]
    expect(patient.locator('[name="answer_1"][value="unanswered"]')).to_be_checked()
    expect(patient.locator('[name="answer_1"][value="0"]')).not_to_be_checked()
    patient.locator('[name="answer_1"][value="0"]').focus()
    patient.keyboard.press("Space")
    expect(patient.locator("[data-questionnaire-state]")).to_have_text("Ответ выбран")
    patient.get_by_text("Добавить комментарий или обсудить", exact=True).click()
    patient.get_by_role("textbox", name="Комментарий к вопросу 1", exact=True).fill(
        "Синтетический комментарий <пример>"
    )
    patient.get_by_label("! Хочу обсудить", exact=False).check()
    save("Сохранить и сделать паузу")
    patient.reload()
    expect(patient.locator('[name="answer_1"][value="0"]')).to_be_checked()
    expect(patient.get_by_label("! Хочу обсудить", exact=False)).to_be_checked()
    expect(
        patient.get_by_role("textbox", name="Комментарий к вопросу 1", exact=True)
    ).to_have_value("Синтетический комментарий <пример>")
    save("Сохранить и продолжить")
    choose(2, "deferred")
    save("Сохранить и продолжить")
    choose(3, "declined")
    save("Сохранить и продолжить")
    choose(4, 2)
    save("Завершить и отправить")
    doctor.goto(phq4_url)
    expect(doctor.locator("#item-1 .is-selected")).to_have_text(
        "✓Выбранный ответ: Совсем нет"
    )
    expect(doctor.locator("#item-2 .is-selected, #item-3 .is-selected")).to_have_count(
        0
    )
    expect(doctor.locator("#item-2")).to_contain_text("Отложено")
    expect(doctor.locator("#item-3")).to_contain_text("Не готов(а) отвечать")
    expect(doctor.locator('input[name^="answer_"]')).to_have_count(0)
    assert patient.request.get(url + "/clinical-tools?patient_id=1").status == 403
    csrf = patient.locator('input[name="csrf"]').first.input_value()
    assert (
        patient.request.post(
            phq4_url + "/review", form={"csrf": csrf, "intent": "read"}
        ).status
        == 403
    )

    assign("PHQ-ADS")
    phq9_url = open_assessment("PHQ-9")
    for item in range(1, 10):
        choose(item, 1 if item == 9 else 0)
        if item == 9:
            expect(patient.locator("#save-state")).to_contain_text(
                "отдельного уточнения"
            )
        save("Сохранить и продолжить" if item < 9 else "Завершить и отправить")
    doctor.goto(phq9_url)
    expect(doctor.locator("main")).to_contain_text("Сумма симптомных ответов: 1/27")
    expect(doctor.locator("#item-9 .is-selected")).to_contain_text(
        "В течение нескольких дней"
    )
    doctor.goto(url + "/doctor")
    expect(doctor.locator("#workflow-alerts")).to_contain_text("нужно уточнение")
    gad_url = open_assessment("GAD-7")
    for item in range(1, 8):
        choose(item, 0)
        save("Сохранить и продолжить" if item < 7 else "Завершить и отправить")
    doctor.goto(gad_url)
    expect(doctor.locator("main")).to_contain_text("Сумма симптомных ответов: 0/21")

    assign("HCL-33")
    hcl_url = open_assessment("HCL-33")
    patient.locator('[name="context_today"][value="0"]').check()
    patient.locator('[name="context_usual"][value="1"]').check()
    choose(1, 0)
    save("Сохранить и сделать паузу")
    patient.reload()
    expect(patient.locator('[name="context_today"][value="0"]')).to_be_checked()
    expect(patient.locator('[name="context_usual"][value="1"]')).to_be_checked()
    for item in range(1, 34):
        choose(item, 0)
        save("Сохранить и продолжить" if item < 33 else "Завершить и отправить")
    doctor.goto(hcl_url)
    expect(doctor.locator("main")).to_contain_text("Сумма симптомных ответов: 0/33")
    expect(doctor.locator(".is-selected")).to_have_count(35)
    expect(doctor.locator(".is-selected").nth(33)).to_contain_text("Намного хуже")

    # Default assignment remains one action; required dates are reachable if invalid.
    doctor.goto(url + "/clinical-tools?patient_id=1")
    unavailable = doctor.locator(".tool-tile button[disabled]")
    expect(unavailable).to_have_count(3)
    tile = doctor.locator('[data-tool="phq9"]')
    tile.locator("summary").click()
    tile.locator('[name="due_day"]').fill("")
    tile.locator("summary").click()
    tile.get_by_role("button", name="Назначить PHQ-9", exact=True).click()
    expect(tile.locator("details")).to_have_attribute("open", "")
    expect(tile.locator('[name="due_day"]')).to_be_focused()

    # Without JS the native radios, comments and form POST still work.
    nojs = patient.context.browser.new_context(java_script_enabled=False)
    nojs.add_cookies(patient.context.cookies())
    native = nojs.new_page()
    native.goto(phq4_url + "?item=2")
    native.locator('[name="answer_2"][value="0"]').check()
    native.get_by_role("button", name="Сохранить и сделать паузу", exact=True).click()
    native.reload()
    expect(native.locator('[name="answer_2"][value="0"]')).to_be_checked()
    nojs.close()

    audits = []
    routes = [
        (patient, "patient-catalog", "/patients/1/profile/assessments"),
        (patient, "patient-question", phq9_url.removeprefix(url) + "?item=1"),
        (patient, "patient-safety", safety_url.removeprefix(url) + "?item=6"),
        (patient, "patient-context", hcl_url.removeprefix(url) + "?item=1"),
        (doctor, "doctor-catalog", "/clinical-tools?patient_id=1"),
        (doctor, "doctor-assessments", "/patients/1/workspace/assessments"),
        (doctor, "doctor-answers", phq9_url.removeprefix(url)),
        (doctor, "doctor-safety", "/patients/1/safety"),
    ]
    for page, label, path in routes:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            response = page.goto(url + path)
            assert response.status == 200, (label, response.status)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            ), (label, width)
            if width in (390, 1280):
                page.screenshot(path=str(out / f"{label}-{width}.png"), full_page=True)
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',
                    values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations
                    .map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(page=label, width=width, violations=violations))
                assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS questionnaires: real native POST/reload; zero/deferred/declined; "
        "comment/discuss; safety branching and recent-behavior follow-up; PHQ-ADS "
        "assignment, PHQ-9 item 9 alert, GAD-7, all 33 HCL symptoms with separate "
        "context; patient restrictions and clinician read-only chips; hidden date "
        "validation; no-JS save; both roles at 320/390/768/1280; "
        "16 axe checks, no JavaScript errors. Synthetic disposable DB only.",
        flush=True,
    )
