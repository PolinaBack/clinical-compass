"""Four visible patient-specific cards, with Ollama absent, on synthetic C/D/E."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_aviandr_conclusions(doctor, second, patient, url):
    out = Path("artifacts/aviandr-conclusions")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits, summaries = [], [], {}
    for page in (doctor, second, patient):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.emulate_media(reduced_motion="reduce")
    routes = [(doctor, "C", 1), (doctor, "D", 2), (second, "E", 3)]
    for page, letter, pid in routes:
        for width in (320, 390, 768, 1280, 1920):
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(f"{url}/patients/{pid}/ai-analysis").status == 200
            block = page.locator("[data-aviandr-analysis]")
            expect(block.locator(".aviandr-direction")).to_have_count(4)
            expect(block.locator("[data-conclusion]")).to_have_count(4)
            assert block.locator("[data-conclusion]").evaluate_all(
                "nodes=>nodes.every(node=>!node.closest('details')&&node.textContent.trim().length>100)"
            )
            for card in block.locator(".aviandr-direction").all():
                expect(card.locator("[data-basis] > li")).to_have_count(3)
                expect(card.locator("[data-card-questions] > li")).to_have_count(2)
            columns = block.locator(".aviandr-directions").evaluate(
                "node=>getComputedStyle(node).gridTemplateColumns.split(' ').length"
            )
            assert columns == (1 if width <= 650 else 2), (letter, width, columns)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                letter,
                width,
            )
            expect(block.locator("[data-trend]")).to_have_count(3)
            expect(block.locator("[data-questionnaire-trend]")).to_have_count(3)
            expect(block).to_contain_text("Расчёт от")
            if width in (390, 1280):
                block.locator(".aviandr-directions").screenshot(
                    path=str(out / f"cards-{letter}-{width}.png"),
                    style=".site-header{visibility:hidden!important}",
                )
                block.locator(".aviandr-heading").evaluate(
                    "node=>window.scrollTo({top:scrollY+node.getBoundingClientRect().top-100,behavior:'instant'})"
                )
                page.screenshot(path=str(out / f"screen-{letter}-{width}.png"))
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(scenario=letter, width=width, violations=violations))
                assert not violations, audits[-1]
            if width == 1280:
                summaries[letter] = block.locator(
                    "[data-conclusion]"
                ).all_text_contents()
                block.locator("[data-basis] a").first.click()
                expect(block.locator("#av-details-indications")).to_have_attribute(
                    "open", ""
                )
                expect(
                    block.locator("#av-evidence-P-indications-context")
                ).to_be_visible()
                block.locator("#av-details-indications > summary").click()
                block.locator("#av-trends").screenshot(
                    path=str(out / f"trends-{letter}.png"),
                    style=".site-header{visibility:hidden!important}",
                )
    for index in range(4):
        assert len({summaries[k][index] for k in summaries}) == 3
    assert "Эсциталопрам" in summaries["D"][2] and "Спитомин" in summaries["D"][2]
    assert "14" in summaries["E"][3] and "Тошнота" in summaries["E"][3]

    doctor.goto(url + "/patients/2/ai-analysis")
    before = doctor.locator("#av-contraindications [data-conclusion]").text_content()
    form = doctor.locator('form[action$="/aviandr/facts"]')
    form.locator("..").locator(":scope > summary").click()
    form.locator('[name="kind"]').select_option("age")
    form.locator('[name="value"]').fill("48")
    form.locator('[name="note"]').fill(
        "Синтетическое уточнение возраста для проверки пересчёта карточки."
    )
    form.locator('[name="confirmed"]').check()
    form.get_by_role("button", name="Сохранить сведение", exact=True).click()
    expect(doctor.locator("#av-contraindications [data-conclusion]")).to_contain_text(
        "48"
    )
    assert (
        doctor.locator("#av-contraindications [data-conclusion]").text_content()
        != before
    )

    native_context = doctor.context.browser.new_context(java_script_enabled=False)
    native_context.add_cookies(doctor.context.cookies())
    native = native_context.new_page()
    assert native.goto(url + "/patients/1/ai-analysis").status == 200
    expect(native.locator("[data-conclusion]")).to_have_count(4)
    native.locator("#av-details-indications > summary").click()
    expect(native.locator("#av-evidence-P-indications-context")).to_be_visible()
    native_context.close()
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    (out / "browser-conclusions.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2)
    )
    print(
        "PASS four visible individualized Python conclusions for C/D/E, two-drug D, 2x2 desktop/one column mobile, concrete fact reveal, three diary and three questionnaire charts, live age update, no-JS details, five widths, six axe A/AA audits, no JS errors; synthetic DB, no Ollama required.",
        flush=True,
    )
