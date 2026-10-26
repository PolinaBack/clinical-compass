"""Readability and automatic actual-response display, on synthetic test data only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_ai_presentation(page, url):
    out = Path("artifacts/ai-presentation")
    errors = []
    audits = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/patients/1/ai-analysis")
    page.locator('form[action="/patients/1/ai-analysis/run"] button').click()
    initial_url = page.url
    expect(page.locator("#patient-ai-result")).to_be_visible(timeout=20000)
    assert page.url == initial_url
    assert (
        page.evaluate(
            "document.querySelector('[data-ai-result-area=general] > [data-patient-ai]').nextElementSibling.id"
        )
        == "patient-ai-result"
    )
    expect(page.locator("#patient-ai-result")).to_contain_text("БРАУЗЕРНАЯ ФИКСТУРА")
    assert page.evaluate("window.__aiInjected===undefined")
    for text in [
        "Ответы, требующие отдельного уточнения",
        "Регистрационные сведения ГРЛС",
        "Исходные анкеты и сведения карты",
        "Проверенные источники и правила",
        "История текстовых объяснений",
        "Модель и версия анализа",
        "Сведения, переданные модели",
        "Подробнее",
    ]:
        expect(page.locator("main")).not_to_contain_text(text)
    expect(
        page.locator("#patient-ai-result a,#av-trends a,#av-questions a")
    ).to_have_count(0)
    expect(page.locator("[data-trend]")).to_have_count(3)
    expect(page.locator("[data-questionnaire-trend]")).to_have_count(0)
    assert page.locator("[data-gap-connection]").count() >= 4
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            page.locator("#patient-ai-result").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"summary-{width}.png"))
            page.locator(".aviandr-insights-layout").scroll_into_view_if_needed()
            page.screenshot(path=str(out / f"trends-{width}.png"))
            page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = page.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, violations=violations))
            assert not violations, audits[-1]
    page.locator('form[action="/patients/1/ai-analysis/aviandr/run"] button').click()
    initial_url = page.url
    expect(page.locator("[data-aviandr-result]")).to_be_visible(timeout=20000)
    assert page.url == initial_url
    expect(page.locator("[data-aviandr-result]")).to_contain_text("БРАУЗЕРНАЯ ФИКСТУРА")
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS: actual synthetic model transcripts appear automatically in both result areas; summary immediately follows status; removed panels and evidence links absent; only three trends; lines bridge recorded points without zero filling; five widths; two axe A/AA audits; no script execution or JS errors."
    )
