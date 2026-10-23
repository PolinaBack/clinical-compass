"""Anamnesis UI acceptance on a caller-owned disposable database; requires COMPASS_AXE_PATH."""

from pathlib import Path
from playwright.sync_api import expect
import json, os, time


def check_intake_redesign(patient, doctor, url):
    out = Path("artifacts/intake-redesign")
    out.mkdir(exist_ok=True, parents=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda e: errors.append(str(e)))
    patient.goto(url + "/patients/1/profile/about")
    expect(patient.locator(".intake-percent")).to_have_text("0% заполнено")
    expect(patient.locator("a.intake-category")).to_have_count(15)
    assert "Менструальный" not in patient.locator("main").inner_text()
    assert (
        patient.locator("[data-intake-intro]").bounding_box()["y"]
        < patient.locator(".intake-overall").bounding_box()["y"]
    )
    assert (
        patient.locator("[data-intake-privacy]").bounding_box()["y"]
        > patient.locator("a.intake-category").last.bounding_box()["y"]
    )
    expect(patient.locator('textarea[name="biography"]')).to_have_count(0)
    patient.locator("[data-intake-jump-select]").select_option("personal")
    expect(patient).to_have_url(url + "/patients/1/profile/about/personal")
    assert patient.evaluate("document.documentElement.scrollHeight") < 2000
    patient.locator('[name="a_preferred_name"]').fill("Синтетическое обращение")
    patient.locator('[name="a_sex"]').select_option("Женский")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    expect(
        patient.locator('[data-intake-jump-select] option[value="reproductive"]')
    ).to_have_count(1)
    patient.locator("[data-intake-jump-select]").select_option("reproductive")
    expect(patient).to_have_url(url + "/patients/1/profile/about/reproductive")
    patient.locator('[name="a_menstruation"]').select_option("Да")
    patient.locator('[name="a_cycle_days"]').fill("28")
    patient.locator("[data-intake-jump-select]").select_option("personal")
    expect(patient).to_have_url(url + "/patients/1/profile/about/personal")
    patient.locator('[name="a_sex"]').select_option("Мужской")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    expect(
        patient.locator('[data-intake-jump-select] option[value="reproductive"]')
    ).to_have_count(0)
    assert (
        patient.request.get(url + "/patients/1/profile/about/reproductive").status
        == 404
    )
    patient.goto(url + "/patients/1/profile/about")
    assert "Менструальный" not in patient.locator("main").inner_text()
    patient.locator("[data-intake-jump-select]").select_option("personal")
    patient.locator('[name="a_sex"]').select_option("Женский")
    patient.locator("[data-intake-next]").click()
    patient.goto(url + "/patients/1/profile/about/reproductive")
    expect(patient.locator('[name="a_cycle_days"]')).to_have_value("28")
    # A small status disclosure preserves a distinct zero, unknown and declined.
    patient.goto(url + "/patients/1/profile/about/mental")
    patient.locator('[data-intake-field="mood"] .intake-answer-options summary').click()
    patient.locator('[name="s_mood"]').select_option("unknown")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    patient.reload()
    expect(patient.locator('[name="s_mood"]')).to_have_value("unknown")
    # Jump while an actual autosave is pending; the latest edit must reach SQLite.
    patient.goto(url + "/patients/1/profile/about/personal")
    calls = []

    def delayed(route):
        if route.request.method == "POST":
            calls.append(route.request.headers.get("accept", ""))
            if len(calls) == 1:
                assert "application/json" in calls[0]
                patient.evaluate(
                    """()=>{const i=document.querySelector('[name="a_preferred_name"]');i.value='Последняя правка';i.dispatchEvent(new Event('input',{bubbles:true}));const s=document.querySelector('[data-intake-jump-select]');s.value='daily';s.dispatchEvent(new Event('change',{bubbles:true}));}"""
                )
                time.sleep(1)
        route.continue_()

    patient.route("**/intake/sections/personal", delayed)
    patient.locator('[name="a_preferred_name"]').fill("Первый ответ")
    expect(patient).to_have_url(url + "/patients/1/profile/about/daily", timeout=30000)
    patient.unroute("**/intake/sections/personal", delayed)
    patient.goto(url + "/patients/1/profile/about/personal")
    expect(patient.locator('[name="a_preferred_name"]')).to_have_value(
        "Последняя правка"
    )
    plain_context = patient.context.browser.new_context(
        java_script_enabled=False, reduced_motion="reduce"
    )
    plain_context.add_cookies(patient.context.cookies())
    plain = plain_context.new_page()
    plain.goto(url + "/patients/1/profile/about/personal")
    plain.locator('[name="a_preferred_name"]').fill("Без JavaScript")
    plain.locator("[data-intake-jump-select]").select_option("measurements")
    plain.locator("[data-intake-jump-button]").focus()
    plain.keyboard.press("Enter")
    expect(plain).to_have_url(url + "/patients/1/profile/about/measurements")
    plain_context.close()
    patient.goto(url + "/patients/1/profile/about")
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    doctor.goto(url + "/patients/1/profile/about?view=full")
    expect(doctor.locator("a.intake-category")).to_have_count(16)
    doctor.goto(url + "/patients/1/profile/about/personal")
    expect(doctor.locator("main")).to_contain_text("Без JavaScript")
    audits = []
    for page, role, path in [
        (patient, "overview", "/patients/1/profile/about"),
        (patient, "personal", "/patients/1/profile/about/personal"),
        (patient, "mental", "/patients/1/profile/about/mental"),
        (doctor, "doctor", "/patients/1/profile/about?view=full"),
    ]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.goto(url + path)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (role, width)
            if role == "overview" and width == 1280:
                assert (
                    page.locator(".intake-dashboard-grid").evaluate(
                        'e=>getComputedStyle(e).gridTemplateColumns.split(" ").length'
                    )
                    == 3
                )
            if role == "personal" and width == 390:
                assert page.evaluate("document.documentElement.scrollHeight") < 2500
            page.screenshot(path=str(out / f"{role}-{width}.png"), full_page=True)
            if width in (390, 1280):
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                v = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(role=role, width=width, violations=v))
                assert not v, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS redesign: reference-style cards, intro/privacy order, percentages, no legacy notes, strict female condition with preserved answers, jump during autosave, no-JS keyboard jump, clinician snapshot, 320/390/768/1280, eight axe checks, no JavaScript errors.",
        flush=True,
    )
