"""Real UI writes on disposable fictional accounts, never actual clinical data."""

import os
import json
from pathlib import Path
from playwright.sync_api import expect


def check_medicine_reactions(doctor, patient, url):
    out = Path("artifacts/medicine-reactions")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    audits = []
    dialogs = []
    for page in (doctor, patient):
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/patients/1/assignments")
    doctor.locator("#draft-picker > summary").click()
    for code in ("D01", "D11"):
        doctor.locator(f'[data-pick-draft="{code}"]').click()
        doctor.locator(f'[name="{code}.dose"]').fill("СИНТЕТИЧЕСКАЯ ДОЗА")
        doctor.locator(f'[name="{code}.frequency"]').select_option("1 раз в день")
    doctor.locator("[data-medicine-draft] [value=save]").click()
    doctor.locator('[name="D01.dose"]').fill("СИНТЕТИЧЕСКАЯ ДОЗА-2")
    # Unfilled reaction forms belong to their own HTML forms and must not block saving the scheme.
    doctor.locator("[data-medicine-draft] [value=save]").click()
    expect(doctor.locator('[name="D01.dose"]')).to_have_value("СИНТЕТИЧЕСКАЯ ДОЗА-2")
    card = doctor.locator("[data-draft-item=D01]")
    card.locator(".reaction-report-editor > summary").click()
    card.locator("[name=symptom]").select_option("other")
    card.locator("[name=custom_symptom]").fill("ЗАКРЫТОЕ НАБЛЮДЕНИЕ ВРАЧА")
    card.locator("[name=relation]").select_option("possible")
    card.locator("[name=assessment_note]").fill(
        "Закрытое основание — синтетический пример"
    )
    card.get_by_role("button", name="Сохранить реакцию", exact=True).click()
    expect(doctor.locator("#reaction-report-1")).to_contain_text(
        "ЗАКРЫТОЕ НАБЛЮДЕНИЕ ВРАЧА"
    )
    patient.goto(url + "/patients/1/profile/assignments")
    expect(patient.locator("[data-shared-medicine]")).to_have_count(0)
    doctor.get_by_role(
        "button", name="Отправить учебную схему пациенту", exact=True
    ).click()
    patient.reload()
    expect(patient.locator("[data-shared-medicine]")).to_have_count(2)
    expect(patient.locator("main")).not_to_contain_text("ЗАКРЫТОЕ НАБЛЮДЕНИЕ ВРАЧА")
    med = patient.locator("[data-shared-medicine=D01]")
    expect(med).to_contain_text("СИНТЕТИЧЕСКАЯ ДОЗА-2")
    med.locator(".reaction-report-editor > summary").click()
    med.locator("[name=symptom]").select_option(label="Тошнота · ЖКТ")
    med.locator("[name=onset]").fill("2026-10-01")
    med.locator("[name=impact]").select_option("moderate")
    med.locator("[name=description]").fill("СИНТЕТИЧЕСКОЕ СООБЩЕНИЕ ПАЦИЕНТА")
    med.get_by_role("button", name="Сообщить врачу", exact=True).click()
    expect(patient.locator("#reaction-report-2")).to_contain_text(
        "СИНТЕТИЧЕСКОЕ СООБЩЕНИЕ ПАЦИЕНТА"
    )
    doctor.reload()
    record = doctor.locator("#reaction-report-2")
    expect(record).to_contain_text("Со слов пациента")
    record.locator(".reaction-review-editor > summary").click()
    record.locator("[name=relation]").select_option("possible")
    record.locator("[name=assessment_note]").fill("ЗАКРЫТАЯ ОЦЕНКА")
    record.get_by_role("button", name="Сохранить оценку", exact=True).click()
    patient.reload()
    expect(patient.locator("main")).not_to_contain_text("ЗАКРЫТАЯ ОЦЕНКА")
    record.locator(".reaction-review-editor > summary").click()
    record.locator("[name=relation]").select_option("possible")
    record.locator("[name=assessment_note]").fill("ОТКРЫТАЯ ОЦЕНКА СПЕЦИАЛИСТА")
    record.locator("[name=patient_visible]").check()
    record.get_by_role("button", name="Сохранить оценку", exact=True).click()
    patient.reload()
    expect(patient.locator("#reaction-report-2")).to_contain_text(
        "ОТКРЫТАЯ ОЦЕНКА СПЕЦИАЛИСТА"
    )
    med = doctor.locator("[data-draft-item=D11]")
    med.locator(".reaction-report-editor > summary").click()
    med.locator("[name=symptom]").select_option("other")
    med.locator("[name=custom_symptom]").fill("СИНТЕТИЧЕСКОЕ НАБЛЮДЕНИЕ ВРАЧА")
    med.locator("[name=patient_visible]").check()
    med.get_by_role("button", name="Сохранить реакцию", exact=True).click()
    patient.reload()
    expect(patient.locator("#reaction-report-3")).to_contain_text("Запись врача")
    expect(patient.locator("#reaction-report-3")).to_contain_text(
        "СИНТЕТИЧЕСКОЕ НАБЛЮДЕНИЕ ВРАЧА"
    )

    for width in (320, 390, 768, 1280, 1920):
        for page, name, path in [
            (doctor, "doctor", "/patients/1/assignments"),
            (patient, "patient", "/patients/1/profile/assignments"),
        ]:
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(url + path).status == 200
            card = page.locator(
                "[data-draft-item=D01]"
                if name == "doctor"
                else "[data-shared-medicine=D01]"
            )
            card.locator(".reaction-report-editor > summary").click()
            card.locator("[name=symptom]").select_option("other")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                width,
                name,
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
                card.locator(".reaction-report-editor").evaluate(
                    "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-140,behavior:'instant'})"
                )
                page.screenshot(path=str(out / f"{name}-form-{width}.png"))
                page.locator("#reaction-report-2").evaluate(
                    "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-140,behavior:'instant'})"
                )
                page.screenshot(path=str(out / f"{name}-report-{width}.png"))
    doctor.goto(url + "/patients/1/assignments")
    doctor.locator("[data-remove-draft=D01]").click()
    doctor.locator("[data-medicine-draft] [value=save]").click()
    doctor.get_by_role(
        "button", name="Отправить учебную схему пациенту", exact=True
    ).click()
    patient.goto(url + "/patients/1/profile/assignments")
    expect(patient.locator(".shared-plan-history")).to_have_count(1)
    patient.locator(".shared-plan-history > summary").click()
    expect(patient.locator("#reaction-report-2")).to_contain_text(
        "СИНТЕТИЧЕСКАЯ ДОЗА-2"
    )
    doctor.goto(url + "/patients/2/assignments")
    expect(doctor.locator(".medicine-reaction-record")).to_have_count(0)
    assert not errors, errors
    assert not dialogs, dialogs
    print(
        "PASS: doctor saved/changed scheme with unfilled independent reaction forms, private observation, explicit educational share, patient registry report, private/public clinician review and direct observation; schemes unchanged by reports; historical dose retained after replacement and second patient empty. 5 widths without overflow, 4 axe A/AA audits, no JS or accidental beforeunload dialogs. Fictional temporary DB only.",
        flush=True,
    )
