"""Exercise real forms and workers with explicitly labelled synthetic transport."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_ai_free_text(page, url, pid, finish_job):
    out = Path("artifacts/ai-free-text")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.emulate_media(reduced_motion="reduce")
    assert page.goto(f"{url}/patients/{pid}/ai-analysis").status == 200
    general = page.locator('form[action$="/ai-analysis/run"]')
    expect(general.locator('[name="response_mode"]')).to_have_value("free")
    general.get_by_role("button", name="Проанализировать эту карту", exact=True).click()
    text = (
        "Собственный ответ о данных карты. Упоминание Авиандра не требует прохождения фильтра.\n<script>window.__freeTextInjected=1</script>\n"
        + "X" * 1600
    )
    finish_job("general", text)
    link = page.locator("a[data-result-link]")
    expect(link).to_be_visible(timeout=15000)
    link.click()
    result = page.locator("#patient-ai-result [data-model-text]")
    expect(result).to_have_text(text)
    expect(page.locator("#patient-ai-result")).to_contain_text(
        "Непроверенный ответ модели"
    )
    expect(page.locator("#patient-ai-result")).to_contain_text("ТРАНСПОРТНАЯ ФИКСТУРА")
    assert page.evaluate("typeof window.__freeTextInjected") == "undefined"
    av_form = page.locator('form[action$="/aviandr/run"]')
    expect(av_form.locator('[name="response_mode"]')).to_have_value("free")
    av_form.get_by_role(
        "button", name="Объяснить этот расчёт с помощью ИИ", exact=True
    ).click()
    av_text = "Свободное объяснение Авиандра по синтетической карте.\nЭто начало ответа без обязательного JSON."
    finish_job("aviandr", av_text, incomplete=True)
    refresh = page.locator(".aviandr-job [data-result-link]")
    expect(refresh).to_be_visible(timeout=15000)
    refresh.click()
    block = page.locator("[data-aviandr-result]")
    expect(block.locator("[data-model-text]")).to_have_text(av_text)
    expect(block).to_contain_text("Получена только часть ответа")
    expect(block).to_contain_text("Содержание и ссылки автоматически не проверялись.")
    expect(page.locator(".aviandr-direction")).to_have_count(4)
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            block.screenshot(path=str(out / f"answer-{width}.png"))
            page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = page.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, violations=violations))
            assert not violations, audits[-1]
    mode = page.locator('form[action$="/aviandr/run"] [name="response_mode"]')
    mode.select_option("structured")
    mode.focus()
    previous = page.locator("[data-av-checksum]").get_attribute("data-av-checksum")
    finish_job("change", "")
    page.wait_for_function(
        "old=>document.querySelector('[data-av-checksum]').dataset.avChecksum!==old",
        arg=previous,
        timeout=15000,
    )
    expect(
        page.locator('form[action$="/aviandr/run"] [name="response_mode"]')
    ).to_have_value("structured")
    assert page.locator('form[action$="/aviandr/run"] [name="response_mode"]').evaluate(
        "node=>node===document.activeElement"
    )
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS: two default free-text forms, actual queued workers with labelled test transport, verbatim text/XSS escaping, partial response, unchanged cards, five widths, two axe A/AA audits, mode/focus preserved on live update; no JS errors"
    )
