"""Patient-card AI acceptance on caller-owned synthetic accounts and temporary DBs."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_patient_ai(patient, doctor, url, process):
    out = Path("artifacts/patient-ai")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda e: errors.append(str(e)))
    patient.goto(url + "/patients/1/diary")
    patient.locator('[data-open="wellbeing-dialog"]').click()
    patient.get_by_label("Самочувствие, 0–10", exact=True).fill("6")
    patient.get_by_role("button", name="Применить оценку", exact=True).click()
    patient.locator('[data-open="sleep-dialog"]').click()
    patient.get_by_label("Продолжительность сна, часы", exact=True).fill("7.5")
    patient.get_by_role("button", name="Применить сон", exact=True).click()
    entered = "MANUAL_BROWSER_CARD: беспокоит тревога перед важным собеседованием"
    patient.get_by_label("Заметка", exact=True).fill(entered)
    patient.get_by_role("button", name="Сохранить запись", exact=True).click()
    doctor.goto(url + "/doctor")
    expect(doctor.locator('a[href="/patients/1/ai-analysis"]')).to_be_visible()
    doctor.locator('a[href="/patients/1/ai-analysis"]').click()
    expect(doctor.locator("main")).to_contain_text("данные выбранной карты")
    expect(doctor.locator("main")).not_to_contain_text("Мария Соколова")
    expect(doctor.locator("main")).to_contain_text(entered)
    doctor.get_by_role("button", name="Проанализировать эту карту", exact=True).click()
    first = doctor.locator("[data-patient-ai]").get_attribute("data-status-url")
    process(True)
    expect(doctor.locator("[data-stage]")).to_have_text(
        "Эксперимент завершён", timeout=20000
    )
    doctor.get_by_role("link", name="Обновить результат анализа", exact=True).click()
    expect(doctor.locator("#patient-ai-result")).to_contain_text(
        "ТРАНСПОРТНАЯ ФИКСТУРА"
    )
    expect(
        doctor.get_by_role("heading", name="Что уточнить на консультации", exact=True)
    ).to_be_visible()
    assert patient.request.get(url + "/patients/1/ai-analysis").status == 403
    assert patient.request.get(url + first).status == 403
    assert (
        doctor.request.get(url + first.replace("/patients/1/", "/patients/2/")).status
        == 404
    )
    audits = []
    for width in (320, 390, 768, 1280):
        doctor.set_viewport_size(dict(width=width, height=900))
        doctor.goto(url + "/patients/1/ai-analysis")
        assert doctor.evaluate(
            "document.documentElement.scrollWidth<=innerWidth+1"
        ), width
        doctor.screenshot(
            path=str(out / f"card-labelled-fixture-{width}.png"), full_page=True
        )
        doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
        violations = doctor.evaluate(
            """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
        )
        audits.append(
            dict(
                page="card-result-labelled-fixture", width=width, violations=violations
            )
        )
        assert not violations, audits[-1]
    patient.goto(url + "/patients/1/diary")
    patient.get_by_label("Новая заметка", exact=True).fill(
        "SECOND_MANUAL_UPDATE: хочу обсудить новые трудности со сном"
    )
    patient.get_by_role("button", name="Сохранить заметку", exact=True).click()
    doctor.goto(url + "/patients/1/ai-analysis")
    expect(doctor.locator("main")).to_contain_text(
        "Карта изменилась после этого снимка"
    )
    doctor.get_by_role("button", name="Проанализировать эту карту", exact=True).click()
    process(False)
    expect(doctor.locator("[data-stage]")).to_have_text(
        "Эксперимент не завершён", timeout=20000
    )
    doctor.get_by_role("link", name="Обновить результат анализа", exact=True).click()
    expect(doctor.locator("[data-error]")).to_contain_text("Нет соединения")
    expect(doctor.locator("#patient-ai-result")).to_have_count(0)
    expect(doctor.locator("main")).to_contain_text("SECOND_MANUAL_UPDATE")
    expect(doctor.locator("main")).not_to_contain_text("Мария Соколова")
    for width in (390, 1280):
        doctor.set_viewport_size(dict(width=width, height=900))
        doctor.screenshot(
            path=str(out / f"card-real-unavailable-{width}.png"), full_page=True
        )
        doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
        violations = doctor.evaluate(
            """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
        )
        audits.append(
            dict(page="card-real-http-refusal", width=width, violations=violations)
        )
        assert not violations, audits[-1]
    doctor.goto(url + "/patients/2/ai-analysis")
    expect(
        doctor.get_by_role("button", name="Проанализировать эту карту", exact=True)
    ).to_be_disabled()
    expect(doctor.locator("main")).not_to_contain_text("MANUAL_BROWSER_CARD")
    expect(doctor.locator("main")).not_to_contain_text("SECOND_MANUAL_UPDATE")
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS card AI: manual diary/note entry reaches the selected card; payload asserted by worker; no Maria/demo substitution; history, card edits/stale result, queue/poll, separate patients/ACL, labelled transport fixture and REAL HTTP refusal without fallback; 320/390/768/1280; 6 axe checks; no overflow/JS errors. Real model quality not tested.",
        flush=True,
    )
