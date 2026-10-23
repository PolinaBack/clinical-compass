"""Visual anamnesis controls, on a caller-owned synthetic database only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_intake_controls(patient, doctor, url):
    out = Path("artifacts/intake-controls")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda e: errors.append(str(e)))

    def field(page, key):
        return page.locator(f'[data-intake-field="{key}"]')

    def saved(page):
        expect(page.locator("[data-intake-save-status]")).to_contain_text(
            "Черновик сохранён", timeout=20000
        )

    patient.goto(url + "/patients/1/profile/about/complaints")
    boxes = field(patient, "groups").get_by_role("checkbox")
    boxes.nth(0).check()
    boxes.nth(2).check()
    saved(patient)
    patient.reload()
    expect(field(patient, "groups").locator("input:checked")).to_have_count(2)
    patient.goto(url + "/patients/1/profile/about/mental")
    mood = field(patient, "mood")
    mood.get_by_role("radio", name="0", exact=True).check()
    saved(patient)
    patient.reload()
    expect(
        field(patient, "mood").get_by_role("radio", name="0", exact=True)
    ).to_be_checked()
    field(patient, "mood").get_by_role("button", name="Не знаю", exact=True).click()
    saved(patient)
    patient.reload()
    expect(field(patient, "mood").locator("[data-intake-state]")).to_have_value(
        "unknown"
    )
    expect(
        field(patient, "mood").locator('[data-intake-set-state="unknown"]')
    ).to_have_attribute("aria-pressed", "true")
    # Hiding an unanswered field must not squeeze its footer beside the legend.
    for width in (320, 390, 768, 1280):
        patient.set_viewport_size(dict(width=width, height=900))
        assert patient.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), (
            "unknown",
            width,
        )
    field(patient, "mood").get_by_role("button", name="Ответить", exact=True).click()
    field(patient, "mood").get_by_role("radio", name="0", exact=True).check()
    field(patient, "mood").get_by_role("radio", name="0", exact=True).focus()
    patient.keyboard.press("ArrowRight")
    saved(patient)
    expect(
        field(patient, "mood").get_by_role("radio", name="1", exact=True)
    ).to_be_checked()
    field(patient, "mood").get_by_role("radio", name="5", exact=True).check()
    field(patient, "anxiety").get_by_role(
        "button", name="Пропустить", exact=True
    ).click()
    saved(patient)
    patient.reload()
    expect(field(patient, "anxiety").locator("[data-intake-state]")).to_have_value(
        "skipped"
    )
    field(patient, "anxiety").get_by_role("button", name="Ответить", exact=True).click()
    field(patient, "anxiety").get_by_role("radio", name="7", exact=True).check()
    saved(patient)
    patient.goto(url + "/patients/1/profile/about/personal")
    field(patient, "sex").get_by_role("radio", name="Женский", exact=True).check()
    saved(patient)
    patient.goto(url + "/patients/1/profile/about/reproductive")
    field(patient, "menstruation").get_by_role("radio", name="Да", exact=True).check()
    patient.locator('[name="a_cycle_days"]').fill("28")
    saved(patient)
    field(patient, "menstruation").get_by_role("radio", name="Нет", exact=True).check()
    saved(patient)
    expect(patient.locator('[data-intake-field="cycle_days"]')).to_be_hidden()
    field(patient, "menstruation").get_by_role("radio", name="Да", exact=True).check()
    saved(patient)
    expect(patient.locator('[name="a_cycle_days"]')).to_have_value("28")
    patient.get_by_role("link", name="✎ Комментарий", exact=True).click()
    expect(patient.locator('[name="a_comment"]')).to_be_focused()
    patient.locator('[name="a_comment"]').fill("Синтетический комментарий")
    saved(patient)
    patient.get_by_role("link", name="Файл", exact=True).click()
    expect(patient.locator("[data-record-file]")).to_be_focused()
    patient.get_by_role("link", name="Голосовая заметка", exact=True).click()
    expect(patient.locator("[data-record-start]")).to_be_visible()
    # Keyboard-only, native form submission without JavaScript.
    plain_context = patient.context.browser.new_context(
        java_script_enabled=False, reduced_motion="reduce"
    )
    plain_context.add_cookies(patient.context.cookies())
    plain = plain_context.new_page()
    plain.goto(url + "/patients/1/profile/about/mental")
    field(plain, "mood").get_by_role("radio", name="0", exact=True).check()
    plain.get_by_role("button", name="Сохранить раздел", exact=True).focus()
    plain.keyboard.press("Enter")
    expect(
        field(plain, "mood").get_by_role("radio", name="0", exact=True)
    ).to_be_checked()
    plain_context.close()
    patient.goto(url + "/patients/1/profile/about")
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    doctor.goto(url + "/patients/1/profile/about/mental")
    expect(doctor.locator("main")).to_contain_text("Настроение за последние две недели")
    expect(doctor.locator("[data-intake-set-state]")).to_have_count(0)
    # Every visible section uses the common banner; representative controls at all widths.
    audits = []
    paths = [
        ("complaints", "/patients/1/profile/about/complaints"),
        ("mental", "/patients/1/profile/about/mental"),
        ("cycle", "/patients/1/profile/about/reproductive"),
        ("health", "/patients/1/profile/about/health"),
        ("entity", "/patients/1/intake/entities/new/condition"),
    ]
    for label, path in paths:
        for width in (320, 390, 768, 1280):
            patient.set_viewport_size(dict(width=width, height=900))
            patient.goto(url + path)
            expect(patient.locator(".intake-category-banner")).to_have_count(1)
            assert patient.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (label, width)
            if width in (390, 1280):
                patient.screenshot(
                    path=str(out / f"{label}-{width}.png"), full_page=True
                )
                patient.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = patient.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(page=label, width=width, violations=violations))
                assert not violations, audits[-1]
    patient.goto(url + "/patients/1/profile/about")
    section_urls = patient.locator("a.intake-category").evaluate_all(
        'els=>els.map(e=>e.getAttribute("href"))'
    )
    for path in section_urls:
        if path.endswith("/family"):
            continue
        patient.goto(url + path)
        expect(patient.locator(".intake-category-banner")).to_have_count(1)
    doctor.set_viewport_size(dict(width=390, height=900))
    doctor.goto(url + "/patients/1/profile/about/mental")
    assert doctor.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
    violations = doctor.evaluate(
        """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
    )
    audits.append(dict(page="doctor", width=390, violations=violations))
    assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS visual controls: common banners, native chips/multiple choice/0–10, 0 versus unknown/skipped, keyboard, branches, reload, no JS, comment/file links, sent clinician view, every section, four widths; 11 axe checks; no JavaScript errors.",
        flush=True,
    )
