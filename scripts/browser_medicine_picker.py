"""Patient preferences before drug selection; disposable fictional records only."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_medicine_picker(doctor, patient, url):
    out = Path("artifacts/medicine-picker")
    out.mkdir(parents=True, exist_ok=True)
    errors, dialogs, audits = [], [], []
    for page in (doctor, patient):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.emulate_media(reduced_motion="reduce")
    doctor.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))

    doctor.goto(url + "/patients/2/assignments")
    doctor.locator("#draft-picker > summary").click()
    expect(doctor.locator('[data-picker-preferences="D01"]')).to_have_attribute(
        "data-check-state", "not_selected"
    )
    with doctor.expect_popup() as opened:
        doctor.locator('[data-draft-card="D09"]').click()
    card = opened.value
    expect(card.get_by_role("heading", name="Агомелатин", exact=True)).to_be_visible()
    expect(
        card.get_by_role("link", name="Назначить пациенту", exact=True)
    ).to_have_attribute("href", "/medicines/prescriptions?add=D09&patient_id=2")
    card.get_by_role(
        "link", name="← Вернуться к выбранному разделу", exact=True
    ).click()
    expect(card).to_have_url(url + "/patients/2/assignments#medicines")
    card.close()

    patient.goto(url + "/patients/1/profile/assignments")
    patient.locator(".med-pref-questionnaire > summary").click()
    pref = patient.locator("[data-med-preferences]")
    for code in ("S014", "S131", "S001"):
        pref.locator(f'[data-pref-option]:has([value="{code}"])').click()
    pref.locator('[name="primary"][value="S014"]').check()
    pref.get_by_role(
        "button", name="Отправить ответы специалисту →", exact=True
    ).click()
    expect(patient.locator(".med-pref-feedback")).to_contain_text("Ответы отправлены")

    doctor.goto(url + "/patients/1/assignments")
    doctor.locator("#draft-picker > summary").click()
    expect(doctor.locator("[data-draft-item]:visible")).to_have_count(0)
    check = doctor.locator('[data-picker-preferences="D01"]')
    expect(check).to_have_attribute("data-check-state", "matches")
    expect(check).to_contain_text("совпадений 1 из 3")
    expect(check.locator('[data-picker-effect="S014"]')).to_contain_text("★ Тошнота")
    expect(check.locator('[data-picker-effect="S014"]')).to_contain_text(
        "Есть в реестре"
    )
    expect(check.locator('[data-picker-effect="S131"]')).to_contain_text(
        "Термин найден в другой системе: Нервная система"
    )
    expect(check.locator('[data-picker-effect="S001"]')).to_contain_text(
        "В реестре не найдено"
    )
    doctor.locator('[data-pick-draft="D01"]').click()
    doctor.locator('[name="D01.dose"]').fill("СИНТЕТИЧЕСКАЯ ДОЗА")
    doctor.locator('[name="D01.frequency"]').select_option("1 раз в день")
    # Opening a reference must leave unsaved fields on the original page intact.
    with doctor.expect_popup() as opened:
        doctor.locator('[data-draft-card="D09"]').click()
    card = opened.value
    expect(card.get_by_role("heading", name="Агомелатин", exact=True)).to_be_visible()
    card.close()
    expect(doctor.locator('[name="D01.dose"]')).to_have_value("СИНТЕТИЧЕСКАЯ ДОЗА")
    expect(doctor.locator('[data-pick-draft="D01"]')).to_be_disabled()
    doctor.locator('[data-medicine-draft] [value="save"]').click()
    expect(doctor).to_have_url(url + "/patients/1/assignments?saved=1#medicines")
    doctor.reload()
    expect(doctor.locator('[name="D01.dose"]')).to_have_value("СИНТЕТИЧЕСКАЯ ДОЗА")
    doctor.locator("#draft-picker > summary").click()
    search = doctor.locator("#draft-picker [data-medicine-picker-search]")
    search.fill("агомелатин")
    expect(doctor.locator("#draft-picker [data-picker-row]:visible")).to_have_count(1)
    expect(doctor.locator('[data-draft-card="D09"]')).to_be_visible()
    search.fill("")
    expect(doctor.locator("#draft-picker [data-picker-row]:visible")).to_have_count(12)

    for width in (320, 390, 768, 1280, 1920):
        doctor.set_viewport_size(dict(width=width, height=1000))
        expect(check).to_contain_text("совпадений 1 из 3")
        assert doctor.evaluate(
            "document.documentElement.scrollWidth<=innerWidth"
        ), width
        if width in (390, 1280):
            doctor.locator("#draft-picker").evaluate(
                "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-110,behavior:'instant'})"
            )
            doctor.screenshot(path=str(out / f"picker-{width}.png"))
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',
                values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations
                .map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(
                dict(width=width, page="doctor-picker", violations=violations)
            )
            assert not violations, audits[-1]

    patient.locator(".med-pref-questionnaire > summary").click()
    pref.locator('[data-pref-remove="S014"]').click()
    pref.locator('[data-pref-remove="S131"]').click()
    pref.locator('[name="primary"][value="S001"]').check()
    pref.get_by_role(
        "button", name="Отправить ответы специалисту →", exact=True
    ).click()
    doctor.reload()
    doctor.locator("#draft-picker > summary").click()
    expect(check).to_have_attribute("data-check-state", "not_found")
    expect(check).to_contain_text("совпадений 0 из 1")
    expect(check.locator('[data-picker-effect="S014"]')).to_have_count(0)
    doctor.goto(url + "/patients/1/assignments?version=1")
    expect(doctor.locator("[data-preference-matches=D01]")).to_contain_text("★ Тошнота")
    expect(doctor.locator("#draft-picker")).to_have_count(0)

    # The native reference flow can also add to the correct card without JS.
    nojs = doctor.context.browser.new_context(
        java_script_enabled=False,
        reduced_motion="reduce",
        viewport=dict(width=1280, height=1000),
    )
    nojs.add_cookies(doctor.context.cookies())
    native = nojs.new_page()
    native.goto(url + "/patients/2/assignments")
    native.locator("#draft-picker > summary").click()
    expect(native.locator('[data-picker-preferences="D01"]')).to_have_attribute(
        "data-check-state", "not_selected"
    )
    with native.expect_popup() as opened:
        native.locator('[data-draft-card="D09"]').click()
    card = opened.value
    card.get_by_role("link", name="Назначить пациенту", exact=True).click()
    expect(card).to_have_url(url + "/patients/2/assignments?add=D09#medicines")
    card.locator('[name="D09.dose"]').fill("ТОЛЬКО СИНТЕТИЧЕСКАЯ КАРТА 2")
    card.locator('[name="D09.frequency"]').select_option("1 раз в день")
    card.locator('[data-medicine-draft] [value="save"]').click()
    expect(card).to_have_url(url + "/patients/2/assignments?saved=1#medicines")
    expect(card.locator('[name="D09.dose"]')).to_have_value(
        "ТОЛЬКО СИНТЕТИЧЕСКАЯ КАРТА 2"
    )
    nojs.close()
    assert not errors, errors
    assert not dialogs, dialogs
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS medicine picker: before-addition exact per-effect matches and not-found "
        "states plus same-term/different-system review from patient submission, "
        "primary effect, all 12 candidates, new-tab "
        "reference with unsaved dose retained, correct patient 2 return/prescribing, "
        "real save/reload/search, refreshed sent preferences, immutable history, "
        "separate patient without preferences, no-JS add-and-save via reference, "
        "five widths, two axe checks, no JS errors or unsaved navigation dialogs. "
        "Disposable synthetic DB only.",
        flush=True,
    )
