"""Synthetic browser acceptance on a caller-owned disposable database only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def tab_to(page, selector):
    """Reach an actual form control using keyboard navigation through the page."""
    target = page.locator(selector)
    for _ in range(80):
        page.keyboard.press("Tab")
        if target.evaluate("el => el === document.activeElement"):
            return
    raise AssertionError("Form control is not reachable by keyboard: " + selector)


def check_intake(patient, doctor, url):
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
    patient.goto(url + "/patients/1/profile/about/personal")
    tab_to(patient, '[name="a_preferred_name"]')
    patient.keyboard.type("Синтетическое обращение")
    patient.locator('[name="a_birth_date"]').fill("1990-01-01")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    patient.goto(url + "/patients/1/profile/about/measurements")
    patient.locator('[name="a_height"]').fill("175")
    patient.locator('[name="a_weight"]').fill("75")
    patient.locator('[name="a_measured_at"]').fill("2026-10-01")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    expect(patient.locator("[data-intake-bmi]")).to_have_text("24.5")
    patient.reload()
    expect(patient.locator("[data-intake-bmi]")).to_have_text("24.5")
    patient.goto(url + "/patients/1/profile/about/personal")
    expect(patient.locator('[name="a_preferred_name"]')).to_have_value(
        "Синтетическое обращение"
    )
    doctor.goto(url + "/patients/1/profile/about")
    expect(doctor.locator("#intake-overview")).not_to_contain_text(
        "Синтетическое обращение"
    )
    patient.goto(url + "/patients/1/intake/entities/new/condition")
    patient.locator('[name="a_system"]').select_option("Эндокринная")
    patient.locator('[name="a_name"]').fill("Щитовидка, точное название не помню")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    expect(patient.locator('[name="entity_id"]')).to_have_value("1")
    patient.locator("[data-saved-entity-link]").click()
    expect(patient.locator('[name="a_name"]')).to_have_value(
        "Щитовидка, точное название не помню"
    )
    patient.goto(url + "/patients/1/intake/entities/new/medication?related=1")
    patient.locator('[name="a_name"]').fill("Ави")
    expect(patient.locator("#intake-medication-names option")).not_to_have_count(
        0, timeout=15000
    )
    patient.locator('[name="a_name"]').fill("Условный сообщённый препарат")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    patient.goto(url + "/patients/1/profile/about/exams")
    patient.get_by_text(
        "Добавить документ, фото или голосовую заметку", exact=True
    ).click()
    upload = patient.locator("form[data-local-recording]")
    # An actual browser MediaRecorder with the runner's artificial audio device.
    upload.locator("[data-record-start]").click()
    expect(upload.locator("[data-record-stop]")).to_be_visible(timeout=15000)
    patient.wait_for_timeout(800)
    upload.locator("[data-record-stop]").click()
    expect(upload.locator("[data-record-preview]")).to_be_visible()
    upload.get_by_role(
        "button", name="Сохранить выбранные вложения", exact=True
    ).click()
    expect(
        patient.get_by_role("link", name="voice-note.webm", exact=True)
    ).to_be_visible(timeout=20000)
    patient.goto(url + "/patients/1/profile/about?view=review")
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    expect(
        patient.get_by_text("Анкета отправлена ведущему специалисту.", exact=False)
    ).to_be_visible()
    doctor.reload()
    expect(doctor.locator("#intake-overview")).to_contain_text(
        "Синтетическое обращение"
    )
    doctor.goto(url + "/patients/1/intake/entities/1")
    doctor.get_by_text("Проверить этот факт", exact=True).click()
    review = doctor.locator('form[action="/patients/1/intake/review"]')
    review.locator('[name="status"]').select_option("clarify")
    review.locator('[name="note"]').fill("Уточнить первичный источник")
    review.get_by_role(
        "button", name="Сохранить результат проверки", exact=True
    ).click()
    doctor.goto(url + "/patients/1/diary/questions/new")
    tab_to(doctor, '[name="title"]')
    doctor.keyboard.type("Что помогло сегодня?")
    doctor.get_by_label("Формат ответа", exact=True).select_option("multi")
    doctor.locator('[name="options"]').fill("Прогулка\nМузыка\nОтдых")
    doctor.get_by_role("button", name="Сохранить вопрос пациенту", exact=True).click()
    patient.goto(url + "/patients/1/diary")
    patient.get_by_role("button", name="Что помогло сегодня?", exact=False).click()
    patient.locator('#metric-dialog-1 input[value="Прогулка"]').check()
    patient.locator('#metric-dialog-1 input[value="Музыка"]').check()
    patient.locator("#metric-dialog-1 [data-apply]").click()
    expect(patient.locator("#metric-preview-1")).to_have_text("Прогулка, Музыка")
    for dialog, value in [("wellbeing-dialog", "5"), ("sleep-dialog", "8")]:
        patient.locator(f'[data-open="{dialog}"]').click()
        if dialog == "wellbeing-dialog":
            patient.locator('[name="wellbeing"]').fill(value)
        else:
            patient.locator('[name="sleep"]').fill(value)
        patient.locator(f"#{dialog} [data-apply]").click()
    patient.locator('[data-open="treatment-dialog"]').click()
    patient.locator('[name="adherence"]').select_option("none")
    patient.locator("#treatment-dialog [data-apply]").click()
    patient.get_by_role("button", name="Сохранить запись", exact=True).click()
    expect(patient.locator('article[id^="diary-entry-"]').first).to_contain_text(
        "Прогулка, Музыка"
    )
    doctor.goto(url + "/patients/1/diary")
    expect(doctor.locator('article[id^="diary-entry-"]').first).to_contain_text(
        "Прогулка, Музыка"
    )
    audits = []
    axe = os.getenv("COMPASS_AXE_PATH")
    Path("artifacts").mkdir(exist_ok=True)
    for page, role, paths in [
        (
            patient,
            "patient",
            [
                "/patients/1/profile/about",
                "/patients/1/profile/about/personal",
                "/patients/1/profile/about/health",
                "/patients/1/intake/entities/1",
                "/patients/1/diary",
            ],
        ),
        (
            doctor,
            "doctor",
            [
                "/patients/1/profile/about",
                "/patients/1/profile/about/health",
                "/patients/1/intake/entities/1",
                "/patients/1/diary/questions/new",
                "/patients/1/diary",
            ],
        ),
    ]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            for path in paths:
                response = page.goto(url + path)
                assert response.status == 200, (path, response.status)
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (path, width)
                if axe and width in (390, 1280):
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                    )
                    audits.append(
                        dict(role=role, path=path, width=width, violations=violations)
                    )
                    Path("artifacts/intake-accessibility.json").write_text(
                        json.dumps(audits, ensure_ascii=False, indent=2)
                    )
                if width in (390, 1280) and path in [
                    "/patients/1/profile/about",
                    "/patients/1/diary/questions/new",
                ]:
                    page.screenshot(
                        path=f'artifacts/intake-{role}-{path.split("/")[-1]}-{width}.png',
                        full_page=True,
                        timeout=60000,
                    )
    assert not errors, errors
    assert not any(a["violations"] for a in audits), audits
    print(
        "PASS intake: private autosave/reload, real local MediaRecorder upload, linked condition/medicine, submission/review, drug-name suggestions without prescriptions, custom diary question/answers, both roles, keyboard entry, four widths, accessibility."
    )
