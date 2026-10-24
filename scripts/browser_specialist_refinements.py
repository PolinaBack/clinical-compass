"""Browser acceptance on disposable synthetic accounts supplied by the runner."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_specialist_refinements(doctor, patient, url):
    out = Path("artifacts/specialist-refinements")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    for page in (doctor, patient):
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.emulate_media(reduced_motion="reduce")
    patient.goto(url + "/patients/1/profile/assessments")
    patient.get_by_role("button", name="Начать PHQ-4", exact=True).click()
    for item in range(1, 5):
        patient.locator(f'[name="answer_{item}"][value="3"]').check()
        patient.get_by_role(
            "button",
            name="Сохранить и продолжить" if item < 4 else "Завершить и отправить",
            exact=True,
        ).click()
    patient.goto(url + "/patients/1/profile/assessments")
    expect(patient.locator(".assessment-row")).to_have_count(3)
    expect(patient.locator("main")).to_contain_text("PHQ-9")
    expect(patient.locator("main")).to_contain_text("GAD-7")
    doctor.goto(url + "/doctor")
    expect(doctor.locator(".patient-activity")).to_contain_text("Пройдена анкета PHQ-4")
    expect(doctor.locator(".patient-preview").first).to_contain_text("Демо-пациент А")
    doctor.goto(url + "/patients/1/workspace/assessments")
    for removed in (
        "Предложения для уточнения",
        "Отмеченные ответы и действия",
        "PHQ-4 · новый период",
    ):
        expect(doctor.locator("main")).not_to_contain_text(removed)
    doctor.goto(url + "/doctor/forums")
    buttons = doctor.locator(".forum-entry-grid .button")
    assert buttons.nth(0).get_attribute("class") == buttons.nth(1).get_attribute(
        "class"
    )
    assert (
        abs(buttons.nth(0).bounding_box()["y"] - buttons.nth(1).bounding_box()["y"]) < 2
    )
    doctor.goto(url + "/doctor/calendar?week=2030-01-07")
    doctor.locator('[data-calendar-time="2030-01-07T10:00"]').click()
    form = doctor.locator("#calendar-event-form")
    expect(form.locator('[name="event_kind"]')).to_have_value("free")
    expect(form.locator('[name="url"]')).to_be_disabled()
    expect(form.locator('[name="patient_id"]')).to_be_disabled()
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".event-open")).to_contain_text("10:00–10:50")
    doctor.get_by_role("button", name="+ Назначить встречу", exact=True).click()
    expect(form.locator('[name="date"]')).to_have_value("2030-01-07")
    form.locator('[name="date"]').fill("2030-01-07")
    form.locator('[name="time"]').select_option("14:00")
    form.locator('[name="patient_id"]').select_option("1")
    expect(form.locator('[name="url"]')).to_be_enabled()
    assert form.locator('[name="url"]').evaluate(
        "el=>el.required && !el.checkValidity()"
    )
    form.locator('[name="url"]').fill("https://example.org/synthetic-room")
    form.locator('[name="fallback"]').fill("Синтетический резервный контакт")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".event-planned")).to_contain_text("14:00–14:50")
    # Both explicit actions remain available below the full-day grid.
    expect(doctor.locator('[data-calendar-add="free"]')).to_be_visible()
    expect(doctor.locator('[data-calendar-add="appointment"]')).to_be_visible()
    doctor.get_by_role("button", name="+ Запись прошедшей встречи", exact=True).click()
    form.locator('[name="date"]').fill("2020-01-01")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    doctor.get_by_role("button", name="Подготовить запись встречи", exact=True).click()
    expect(doctor.locator("main")).not_to_contain_text("Как обрабатывается запись")
    expect(doctor.locator("main")).not_to_contain_text(
        "События из анкет и сообщения пациентов"
    )
    for name, route in [
        ("patients", "/doctor"),
        ("calendar", "/doctor/calendar?week=2030-01-07&patient_id=1"),
        ("forums", "/doctor/forums"),
        ("assessments", "/patients/1/workspace/assessments"),
    ]:
        for width in (320, 390, 768, 1280):
            doctor.set_viewport_size(dict(width=width, height=900))
            doctor.goto(url + route)
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (name, width)
            if name == "calendar":
                assert doctor.locator(".calendar-scroll").evaluate(
                    "el=>Math.abs(el.scrollTop-256)<2"
                )
                assert doctor.locator(".calendar-scroll").evaluate(
                    "el=>el.scrollHeight>el.clientHeight"
                ), width
                expect(
                    doctor.locator('[data-calendar-time="2030-01-07T00:00"]')
                ).to_have_count(1)
                expect(
                    doctor.locator('[data-calendar-time="2030-01-07T23:00"]')
                ).to_have_count(1)
            if name == "patients":
                nav = doctor.locator(".portal-nav").bounding_box()
                activity = doctor.locator(".patient-activity").bounding_box()
                assert activity["y"] >= nav["y"] + nav["height"] + 20
            doctor.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(page=name, width=width, violations=violations))
            assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS: real browser PHQ-4 submission -> automatic PHQ-9/GAD-7; recent activity/sorted cards; free hours without link -> mandatory link for appointment; calendar initially at 08:00 with all 24 hours available by scrolling; matching forum buttons; clean recording/questionnaire UI; 320/390/768/1280, 16 axe A/AA audits, no JS errors or page overflow. No models invoked.",
        flush=True,
    )
