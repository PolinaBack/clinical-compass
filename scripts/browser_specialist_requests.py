"""Acceptance against a runner-owned temporary database with fictional accounts."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_specialist_requests(doctor, patient, url):
    out = Path("artifacts/specialist-requests")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    for page in (doctor, patient):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/patients/1/profile")
    preparation = doctor.locator(".profile-preparation")
    assert (
        preparation.evaluate("el=>getComputedStyle(el).backgroundColor")
        == "rgb(232, 244, 237)"
    )
    medicine = preparation.locator("form").filter(
        has=doctor.locator('[name="topic"][value="medicines"]')
    )
    medicine.get_by_role("button", name="Запросить у пациента").click()
    expect(medicine.get_by_role("button", name="Запрос отправлен")).to_be_disabled()
    patient.goto(url + "/patients/1/profile/about")
    expect(patient.locator(".information-request-notice")).to_contain_text("Лекарства")
    expect(patient.locator('.intake-category[href$="/medicines"]')).to_contain_text(
        "По просьбе врача"
    )
    patient.locator(".information-request-notice").get_by_role(
        "link", name="Лекарства и фактический приём"
    ).click()
    patient.locator('[name="a_current"][value="Нет"]').check()
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён"
    )
    doctor.reload()
    expect(medicine.get_by_role("button", name="Запрос отправлен")).to_be_disabled()
    patient.get_by_role(
        "link", name="Проверить заполненное и отправить врачу", exact=True
    ).click()
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    expect(patient.locator(".information-request-notice")).to_have_count(0)
    doctor.reload()
    expect(medicine.get_by_role("button", name="Запросить у пациента")).to_be_enabled()
    preparation.locator("form").filter(
        has=doctor.locator('[name="topic"][value="allergies"]')
    ).get_by_role("button").click()

    doctor.goto(url + "/doctor/calendar?week=2030-01-07")
    grid = doctor.locator(".calendar-scroll")
    assert grid.evaluate("el=>Math.abs(el.scrollTop-256)<2")
    grid.evaluate("el=>el.scrollTop=0")
    expect(
        doctor.locator('[data-calendar-time="2030-01-07T00:00"]')
    ).to_be_in_viewport()
    assert (
        len(
            set(
                doctor.locator(".calendar-add-actions button").evaluate_all(
                    "els=>els.map(el=>getComputedStyle(el).backgroundColor)"
                )
            )
        )
        == 1
    )
    expect(doctor.locator("#calendar-editor summary")).to_have_count(0)
    doctor.locator('[data-calendar-add="free"]').click()
    form = doctor.locator("#calendar-event-form")
    expect(form.locator('[name="event_kind"]')).to_have_attribute("type", "hidden")
    form.locator('[name="date"]').fill("2030-01-07")
    form.locator('[name="time"]').select_option("10:00")
    form.locator('[name="end_time"]').fill("11:15")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".event-open")).to_contain_text("10:00–11:15")
    doctor.locator('[data-calendar-add="appointment"]').click()
    form.locator('[name="time"]').select_option("14:00")
    form.locator('[name="end_time"]').fill("15:20")
    form.locator('[name="time"]').select_option("14:10")
    expect(form.locator('[name="end_time"]')).to_have_value("15:20")
    form.locator('[name="url"]').fill("https://example.org/fictional-room")
    form.locator('[name="fallback"]').fill("Вымышленный резервный контакт")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".event-planned")).to_contain_text("14:10–15:20")
    assert (
        doctor.locator("[data-calendar-past]")
        .locator("xpath=..")
        .get_attribute("class")
        == "button-row calendar-add-actions"
    )

    doctor.goto(url + "/patients/1/diary/questions/new")
    builder = doctor.locator("[data-question-builder]")
    builder.locator('[name="title"]').fill("Что помогло сегодня?")
    builder.locator('[name="unit"]').fill("баллы")
    for kind, selector in [
        ("number", 'input[type="number"]'),
        ("boolean", "select"),
        ("text", "textarea"),
        ("choice", "select"),
        ("multi", 'input[type="checkbox"]'),
        ("time", 'input[type="time"]'),
        ("duration", 'input[type="number"]'),
        ("date", 'input[type="date"]'),
        ("file", 'input[type="file"]'),
        ("voice", 'input[type="file"]'),
        ("scale", 'input[type="range"]'),
    ]:
        builder.locator('[name="kind"]').select_option(kind)
        if kind in ("choice", "multi"):
            builder.locator('[name="options"]').fill("Прогулка\nОтдых")
        expect(
            doctor.locator("[data-builder-preview] " + selector).first
        ).to_be_visible()
        if kind not in ("scale", "number", "duration"):
            expect(builder.locator('[name="unit"]')).to_be_disabled()
    expect(builder.locator('[name="position"], [name="threshold"]')).to_have_count(0)
    builder.locator('[name="kind"]').select_option("choice")
    builder.locator('[name="condition"]').select_option("sleep_lte:7.5")
    builder.get_by_role("button", name="Сохранить вопрос пациенту", exact=True).click()
    doctor.goto(url + "/patients/1/diary/questions/1/edit")
    expect(builder.locator('[name="kind"]')).to_have_value("choice")
    expect(builder.locator('[name="condition"]')).to_have_value("sleep_lte:7.5")
    builder.locator('[name="kind"]').select_option("text")
    builder.locator('[name="condition"]').select_option("always")
    builder.get_by_role("button", name="Сохранить вопрос пациенту", exact=True).click()
    patient.goto(url + "/patients/1/diary")
    patient.locator('[data-diary-question="1"]').click()
    dialog = patient.locator("#metric-dialog-1")
    expect(dialog.locator('[name="metric_1"]')).to_have_js_property(
        "tagName", "TEXTAREA"
    )
    dialog.locator('[name="metric_1"]').fill("Помогла прогулка")
    dialog.locator("[data-apply]").click()
    patient.locator('[data-open="wellbeing-dialog"]').click()
    patient.locator("#wellbeing-value").fill("5")
    patient.locator("#wellbeing-dialog [data-apply]").click()
    patient.locator('[data-open="sleep-dialog"]').click()
    patient.locator('[name="sleep"]').fill("7")
    patient.locator("#sleep-dialog [data-apply]").click()
    patient.get_by_role("button", name="Сохранить запись", exact=True).click()
    expect(patient.locator("main")).to_contain_text("Помогла прогулка")

    for role, page, routes in [
        (
            "doctor",
            doctor,
            [
                ("profile", "/patients/1/profile"),
                ("calendar", "/doctor/calendar?week=2030-01-07"),
                ("builder", "/patients/1/diary/questions/1/edit"),
            ],
        ),
        ("patient", patient, [("requests", "/patients/1/profile/about")]),
    ]:
        for name, route in routes:
            for width in (320, 390, 768, 1280):
                page.set_viewport_size(dict(width=width, height=900))
                page.goto(url + route)
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth+1"
                ), (name, width)
                page.screenshot(
                    path=str(out / f"{role}-{name}-{width}.png"), full_page=True
                )
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(
                    dict(page=name, role=role, width=width, violations=violations)
                )
                assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS: doctor request -> patient private draft -> submission -> request answered; green summary; calendar initial 08:00/night scroll, three matching actions and explicit end times; constructor all 11 previews, saved format change and actual patient text answer; 320/390/768/1280, 16 axe A/AA audits; no overflow/JS errors. Temporary fictional database, no model calls.",
        flush=True,
    )
