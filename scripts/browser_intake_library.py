"""Patient intake and doctor library on a caller-owned disposable database."""

import json
import os
import time
from pathlib import Path
from playwright.sync_api import expect


def check_intake_library(patient, doctor, url):
    artifacts = Path("artifacts/intake-library")
    artifacts.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda e: errors.append(str(e)))
    patient.goto(url + "/patients/1/profile/about/personal")
    expect(patient.locator('[data-intake-field="sex"]')).to_be_visible()
    patient.locator('[data-intake-field="sex"]').get_by_role(
        "radio", name="Женский", exact=True
    ).check()
    patient.locator("[data-intake-next]").click()
    expect(patient).to_have_url(url + "/patients/1/profile/about/measurements")
    patient.goto(url + "/patients/1/profile/about")
    expect(patient.locator('a.intake-category[href$="/reproductive"]')).to_have_count(1)
    expect(patient.locator("[data-optional-intake]")).to_have_count(0)
    patient.goto(url + "/patients/1/profile/about/personal")
    patient.locator('[data-intake-field="sex"]').get_by_role(
        "radio", name="Мужской", exact=True
    ).check()
    patient.locator("[data-intake-next]").click()
    patient.goto(url + "/patients/1/profile/about")
    expect(patient.locator('a.intake-category[href$="/reproductive"]')).to_have_count(0)
    expect(patient.locator("[data-optional-intake]")).to_have_count(0)
    assert (
        patient.request.get(url + "/patients/1/profile/about/reproductive").status
        == 404
    )
    patient.goto(url + "/patients/1/profile/about/personal")
    patient.locator('[data-intake-field="sex"]').get_by_role(
        "radio", name="Женский", exact=True
    ).check()
    patient.locator("[data-intake-next]").click()
    patient.goto(url + "/patients/1/profile/about/reproductive")
    patient.locator('[data-intake-field="menstruation"]').get_by_role(
        "radio", name="Да", exact=True
    ).check()
    patient.locator('[name="a_cycle_days"]').fill("28")
    patient.locator("[data-intake-next]").click()
    patient.goto(url + "/patients/1/profile/about/reproductive")
    expect(patient.locator('[name="a_cycle_days"]')).to_have_value("28")
    patient.reload()
    expect(patient.locator('[name="a_cycle_days"]')).to_have_value("28")

    # Edit again and press Next while a real previous autosave is in flight.
    # A delayed response must not lose the later edit or require a second click.
    patient.goto(url + "/patients/1/profile/about/personal")
    calls = []

    def delayed(route):
        if route.request.method == "POST":
            calls.append(route.request.headers.get("accept", ""))
            if len(calls) == 1:
                assert "application/json" in calls[0], calls
                patient.evaluate(
                    """() => {
                  const input=document.querySelector('[name="a_preferred_name"]');
                  input.value='Последняя правка сохранена';
                  input.dispatchEvent(new Event('input',{bubbles:true}));
                  document.querySelector('[data-intake-next]').click();
                }"""
                )
                time.sleep(1)
        route.continue_()

    pattern = "**/intake/sections/personal"
    patient.route(pattern, delayed)
    patient.evaluate(
        """() => {
      const input=document.querySelector('[name="a_preferred_name"]');
      input.value='Первый ответ';input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));
    }"""
    )
    expect(patient).to_have_url(
        url + "/patients/1/profile/about/measurements", timeout=30000
    )
    patient.unroute(pattern, delayed)
    assert len(calls) >= 2
    patient.goto(url + "/patients/1/profile/about/personal")
    expect(patient.locator('[name="a_preferred_name"]')).to_have_value(
        "Последняя правка сохранена"
    )

    # Plain forms save and continue even without JavaScript.
    plain_context = patient.context.browser.new_context(
        java_script_enabled=False, reduced_motion="reduce"
    )
    plain_context.add_cookies(patient.context.cookies())
    plain = plain_context.new_page()
    plain.goto(url + "/patients/1/profile/about/personal")
    plain.locator('[name="a_preferred_name"]').fill("Без JavaScript")
    plain.locator("[data-intake-next]").focus()
    plain.keyboard.press("Enter")
    expect(plain).to_have_url(url + "/patients/1/profile/about/measurements")
    plain.goto(url + "/patients/1/profile/about/personal")
    expect(plain.locator('[name="a_preferred_name"]')).to_have_value("Без JavaScript")
    plain_context.close()

    # A competing tab cannot be overwritten by Next, including after HTTP 409.
    patient.reload()
    other = patient.context.new_page()
    other.goto(url + "/patients/1/profile/about/personal")
    other.locator('[name="a_preferred_name"]').fill("Ответ из другой вкладки")
    expect(other.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    other.close()
    patient.locator('[name="a_preferred_name"]').fill("Правка в устаревшей вкладке")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Автосохранение остановлено", timeout=20000
    )
    patient.locator("[data-intake-next]").click()
    expect(patient).to_have_url(url + "/patients/1/profile/about/personal")
    expect(patient.locator('[name="a_preferred_name"]')).to_have_value(
        "Правка в устаревшей вкладке"
    )
    # Synthetic unsaved text has just been checked; accept reloading this test tab.
    patient.once("dialog", lambda dialog: dialog.accept())
    patient.reload()
    expect(patient.locator('[name="a_preferred_name"]')).to_have_value(
        "Ответ из другой вкладки"
    )

    doctor.goto(url + "/doctor/library")
    expect(doctor.locator(".library-section")).to_have_count(4)
    for target in ("/medicines", "/doctor/resources", "/clinical-tools"):
        doctor.goto(url + "/doctor/library")
        doctor.locator(f'.library-section[href="{target}"]').click()
        expect(doctor).to_have_url(url + target)
        expect(doctor.locator('.portal-nav a[aria-current="page"]')).to_have_attribute(
            "href", "/doctor/library"
        )
    doctor.goto(url + "/doctor/tasks")
    expect(doctor.locator("main > section").last).to_have_id("faq-forum")
    doctor.get_by_role("link", name="Открыть FAQ-форум", exact=True).click()
    expect(doctor).to_have_url(url + "/doctor/faq")
    expect(doctor.locator('.portal-nav a[aria-current="page"]')).to_have_attribute(
        "href", "/doctor/tasks"
    )
    for path in ("/doctor/library", "/doctor/faq"):
        assert patient.request.get(url + path).status == 403

    axe = os.getenv("COMPASS_AXE_PATH")
    audits = []
    for page, role, path in [
        (patient, "patient", "/patients/1/profile/about/personal"),
        (patient, "patient-cycle", "/patients/1/profile/about/reproductive"),
        (doctor, "doctor", "/doctor/library"),
        (doctor, "doctor-tasks", "/doctor/tasks"),
    ]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.goto(url + path)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (role, width)
            page.screenshot(path=str(artifacts / f"{role}-{width}.png"), full_page=True)
            if axe and width in (390, 1280):
                page.add_script_tag(path=axe)
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(role=role, width=width, violations=violations))
                assert not violations, audits[-1]
    assert not errors, errors
    (artifacts / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "Intake/library browser: sex, voluntary cycle, save-and-continue during autosave, conflict protection, no JS with keyboard, persistence, four library cards, FAQ, role checks, four widths; "
        + (f"{len(audits)} axe audits passed." if axe else "axe not requested.")
        + " JavaScript errors: 0."
    )
