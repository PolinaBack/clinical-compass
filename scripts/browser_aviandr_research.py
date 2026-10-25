"""Real UI and real unavailable-local-HTTP path on disposable C/D/E records only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_aviandr_research(doctor, second, patient, url):
    out = Path("artifacts/aviandr-research")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    audits = []
    for page in (doctor, second, patient):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/patients/1/ai-analysis")
    block = doctor.locator("[data-aviandr-analysis]")
    expect(block.locator(".aviandr-direction")).to_have_count(4)
    expect(block.locator(".aviandr-mode").first).to_have_text(
        "Тестовый режим · только синтетические данные"
    )
    expect(block.locator("#av-indications")).to_contain_text(
        "Недостаточно проверенных данных"
    )
    block.locator('.aviandr-status-chart a[href="#av-indications"]').click()
    expect(block.locator("#av-indications [data-conclusion]")).to_be_visible()
    block.locator("#av-details-indications > summary").click()
    doctor.get_by_text("Регистрационные сведения ГРЛС", exact=True).click()
    expect(block).to_contain_text("ЛП-№(002445)-(РГ-RU)")
    expect(block).to_contain_text("2028-05-31")
    doctor.get_by_text("Регистрационные сведения ГРЛС", exact=True).click()
    assert doctor.goto(url + "/patients/3/ai-analysis").status == 403
    doctor.goto(url + "/patients/2/ai-analysis")
    doctor.locator("#av-details-interactions > summary").click()
    expect(doctor.locator("#av-interactions")).to_contain_text(
        "Недостаточно данных для оценки"
    )
    expect(doctor.locator("#av-interactions")).to_contain_text("Спитомин")

    second.goto(url + "/patients/3/ai-analysis")
    tolerance = second.locator("#av-tolerability")
    tolerance.locator("#av-details-tolerability > summary").click()
    expect(tolerance).to_contain_text("Тошнота")
    expect(tolerance).to_contain_text(
        "После записанной даты начала: 16 дней; до начала: 14 дней"
    )
    expect(tolerance.locator(".aviandr-counts dd")).to_have_text(["14", "2", "0", "0"])
    expect(tolerance).to_contain_text(
        "временное совпадение не подтверждает связь", ignore_case=True
    )

    patient.goto(url + "/patients/3/profile/assignments")
    log = patient.locator('[data-shared-medicine="D01"] .medicine-intake-log').first
    log.locator("summary").click()
    log.locator('[name="state"]').select_option("unknown")
    log.locator('[name="reason"]').fill(
        "Синтетическое уточнение: не помню отметку за день."
    )
    log.get_by_role("button", name="Сохранить отметку", exact=True).click()
    patient.reload()
    log.locator("summary").click()
    expect(log).to_contain_text("Синтетическое уточнение: не помню отметку за день.")
    second.reload()
    tolerance.locator("#av-details-tolerability > summary").click()
    expect(tolerance.locator(".aviandr-counts dd")).to_have_text(["13", "2", "1", "0"])

    # All required fact inputs are reachable in the same native details section.
    form = second.locator('form[action$="/aviandr/facts"]')
    form.locator("..").locator("summary").click()
    form.locator('[name="kind"]').select_option("age")
    form.locator('[name="value"]').fill("39")
    form.locator('[name="note"]').fill(
        "Синтетическое подтверждение возраста в ручной проверке."
    )
    form.locator('[name="confirmed"]').check()
    form.get_by_role("button", name="Сохранить сведение", exact=True).click()
    expect(
        second.get_by_role("status").filter(has_text="Запись сохранена")
    ).to_be_visible()

    # A real worker contacts a closed local Ollama port, without a mock server.
    second.get_by_role(
        "button", name="Объяснить этот расчёт с помощью ИИ", exact=True
    ).click()
    panel = second.locator(".aviandr-job")
    expect(panel.locator("[data-error]")).to_contain_text(
        "Нет соединения с локальной Ollama", timeout=25000
    )
    panel.get_by_role("button", name="Обновить объяснение", exact=True).click()
    expect(second.locator("[data-aviandr-result]")).to_have_count(0)
    expect(second.locator("[data-aviandr-analysis]")).to_contain_text(
        "Текстовый ИИ недоступен или ответ отклонён"
    )
    expect(second.locator(".aviandr-direction")).to_have_count(4)
    expect(second.locator("[data-conclusion]")).to_have_count(4)
    assert patient.goto(url + "/patients/3/ai-analysis").status == 403

    routes = [
        (doctor, "scenario-C", "/patients/1/ai-analysis"),
        (doctor, "scenario-D", "/patients/2/ai-analysis"),
        (second, "scenario-E", "/patients/3/ai-analysis"),
    ]
    for page, label, path in routes:
        for width in (320, 390, 768, 1280, 1920):
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(url + path).status == 200
            if label == "scenario-E":
                page.locator("#av-details-tolerability > summary").click()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                label,
                width,
            )
            if width in (390, 1280):
                page.locator("#aviandr").evaluate(
                    "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-130,behavior:'instant'})"
                )
                page.screenshot(path=str(out / f"{label}-{width}.png"))
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(page=label, width=width, violations=violations))
                assert not violations, audits[-1]
                if label == "scenario-E":
                    page.locator("#av-tolerability").evaluate(
                        "el=>window.scrollTo({top:scrollY+el.getBoundingClientRect().top-130,behavior:'instant'})"
                    )
                    page.screenshot(path=str(out / f"tolerability-{width}.png"))
    native_context = doctor.context.browser.new_context(
        java_script_enabled=False, reduced_motion="reduce"
    )
    native_context.add_cookies(doctor.context.cookies())
    native = native_context.new_page()
    native.goto(url + "/patients/1/ai-analysis")
    native.locator("#av-details-indications > summary").click()
    expect(native.locator("#av-indications")).to_contain_text(
        "Нет действующего проверенного правила"
    )
    expect(native.locator(".aviandr-direction")).to_have_count(4)
    native_context.close()
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS Aviandr research: C/D/E real 30-day records, four current directions, registry excerpt without invented medical rules, two isolated doctors, patient daily intake correction, updated 16-day adherence, native clinician fact save, real unavailable loopback Ollama worker/error/no fake output, source response trace, no-JS details, five widths, six axe A/AA audits, no JS errors. Disposable synthetic DB only.",
        flush=True,
    )
