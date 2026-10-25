"""Browser acceptance with disposable fictional accounts and manually entered schemes."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_medication_workspace(doctor, patient, url):
    out = Path("artifacts/medication-workspace")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    audits = []
    for page in (doctor, patient):
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/patients/1/assignments")
    expect(doctor.locator("#medicines")).to_contain_text("Лекарства")
    assert doctor.locator("#medicines").evaluate(
        "el=>el.compareDocumentPosition(document.querySelector('[data-care-form]')) & Node.DOCUMENT_POSITION_FOLLOWING"
    )
    doctor.locator("#draft-picker > summary").click()
    for code in ("D01", "D11"):
        doctor.locator(f'[data-pick-draft="{code}"]').click()
        doctor.locator(f'[name="{code}.dose"]').fill("СИНТЕТИЧЕСКАЯ ДОЗА")
        doctor.locator(f'[name="{code}.frequency"]').select_option("1 раз в день")
    doctor.locator('[data-medicine-draft] [value="save"]').click()
    expect(doctor).to_have_url(url + "/patients/1/assignments?saved=1#medicines")
    doctor.reload()
    expect(doctor.locator('[name="D01.dose"]')).to_have_value("СИНТЕТИЧЕСКАЯ ДОЗА")
    doctor.locator('[data-medicine-draft] [value="check"]').click()
    expect(doctor.locator(".medicine-pair")).to_have_count(1)
    doctor.get_by_role("link", name="← Вернуться к черновику", exact=True).click()
    expect(doctor).to_have_url(url + "/patients/1/assignments#medicines")
    expect(doctor.locator('[name="D11.dose"]')).to_have_value("СИНТЕТИЧЕСКАЯ ДОЗА")
    patient.goto(url + "/patients/1/profile/assignments")
    patient.locator(".med-pref-questionnaire > summary").click()
    pref = patient.locator("[data-med-preferences]")
    expect(pref.locator("[data-pref-count]")).to_have_text("0 из 3")
    assert pref.locator("[data-pref-option]").count() == 138
    assert pref.locator("[data-pref-system] option").count() == 16
    for code in ("S014", "S055", "S072"):
        pref.locator(f'[data-pref-option]:has([value="{code}"])').click()
    pref.locator('[data-pref-option]:has([value="S115"])').click()
    expect(pref.locator("[name=effect]:checked")).to_have_count(3)
    expect(pref.locator("[data-pref-limit]")).to_contain_text("максимум три")
    pref.locator("[name=primary][value=S014]").check()
    pref.locator("[name=comment]").fill(
        "Мне важно сохранять концентрацию — синтетический пример"
    )
    pref.locator("[data-pref-system]").select_option("ЖКТ")
    pref.locator("[data-pref-search]").fill("тошнота")
    expect(pref.locator("[data-pref-option]:visible")).to_have_count(1)
    expect(pref.locator("[data-pref-choice=S055]")).to_be_visible()
    pref.get_by_role("button", name="Ответить позже", exact=True).click()
    doctor.reload()
    expect(doctor.locator(".medicine-preferences")).not_to_contain_text(
        "Мне важно сохранять концентрацию"
    )
    expect(doctor.locator(".medicine-preference-chips")).to_have_count(0)
    patient.reload()
    expect(pref.locator("[data-pref-count]")).to_have_text("3 из 3")
    expect(pref.locator("[name=comment]")).to_have_value(
        "Мне важно сохранять концентрацию — синтетический пример"
    )
    pref.get_by_role(
        "button", name="Отправить ответы специалисту →", exact=True
    ).click()
    expect(patient.locator(".med-pref-feedback")).to_contain_text("Ответы отправлены")
    doctor.reload()
    expect(doctor.locator(".medicine-preferences")).to_contain_text("★ Тошнота")
    expect(doctor.locator(".medicine-preferences")).to_contain_text(
        "Мне важно сохранять концентрацию"
    )
    expect(doctor.locator("[data-preference-matches=D01]")).to_contain_text("★ Тошнота")
    doctor.locator('[data-medicine-draft] [value="save"]').click()
    doctor.goto(url + "/patients/1/assignments?version=1")
    expect(doctor.locator(".medicine-preferences")).not_to_contain_text(
        "Мне важно сохранять концентрацию"
    )
    doctor.goto(url + "/patients/2/assignments")
    expect(doctor.locator("[data-draft-item]:visible")).to_have_count(0)
    expect(doctor.locator(".medicine-preferences")).not_to_contain_text("★ Тошнота")

    for width in (320, 390, 768, 1280, 1920):
        for page, name, path in [
            (doctor, "doctor", "/patients/1/assignments"),
            (patient, "patient", "/patients/1/profile/assignments"),
        ]:
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(url + path).status == 200
            if name == "patient":
                page.locator(".med-pref-questionnaire > summary").click()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                name,
                width,
            )
            if width in (390, 1280):
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                )
                audits.append(dict(width=width, page=name, violations=violations))
                (out / "accessibility.json").write_text(
                    json.dumps(audits, ensure_ascii=False, indent=2)
                )
                assert not violations, audits[-1]
                page.locator(
                    "#medicines" if name == "doctor" else "#medication-preferences"
                ).evaluate(
                    "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-130,behavior:'instant'})"
                )
                page.screenshot(path=str(out / f"{name}-{width}.png"))
                if name == "patient":
                    pref.locator("[data-pref-system]").select_option("ЖКТ")
                    pref.locator(".med-pref-options").evaluate(
                        "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-260,behavior:'instant'})"
                    )
                    page.screenshot(path=str(out / f"choices-{width}.png"))
                else:
                    page.locator(".care-referrals-heading").evaluate(
                        "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-130,behavior:'instant'})"
                    )
                    page.screenshot(path=str(out / f"referrals-{width}.png"))
    assert not errors, errors
    print(
        "PASS: inline doctor medicines above referrals, actual two-drug save/reload/check/back, legacy redirect; patient 138 choices/15 categories, search/filter, max three and primary, private later draft, explicit send/comment, current matches and immutable history, separate patient; 5 widths without overflow, 4 axe A/AA audits, no JS errors. Fictional temporary DB only.",
        flush=True,
    )
