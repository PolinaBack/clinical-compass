"""Check a separately provisioned synthetic AI page with Playwright."""

import json
from pathlib import Path


def check_ai_formatting(page, base_url, output):
    from playwright.sync_api import expect

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/patients/1/ai-analysis")
    expect(page.locator("#patient-ai-result .ai-markdown strong").first).to_have_text(
        "Жирный текст"
    )
    expect(page.locator("#av-explanation .ai-markdown strong").first).to_have_text(
        "Жирный текст"
    )
    assert not page.evaluate("Boolean(window.__aiInjected)")
    assert page.locator(".ai-markdown img, .ai-markdown script").count() == 0
    assert (
        page.locator("#av-explanation .ai-markdown em").first.evaluate(
            "e => getComputedStyle(e).fontStyle"
        )
        == "italic"
    )
    assert (
        int(
            page.locator("#av-explanation .ai-markdown strong").first.evaluate(
                "e => getComputedStyle(e).fontWeight"
            )
        )
        >= 700
    )
    assert page.locator("#av-explanation .ai-markdown table").count() == 1

    for key, decision in [
        ("indications", "gad"),
        ("contraindications", "complete"),
        ("interactions", "confirmed"),
        ("tolerability", "no_effects"),
    ]:
        card = page.locator(f"[data-direction={key}]")
        expect(card).to_have_attribute("data-level", "attention")
        assert (
            card.evaluate(
                "e => getComputedStyle(e).getPropertyValue('--av-tint').trim()"
            )
            == "#fff1c9"
        )
        card.locator(".aviandr-review > summary").click()
        form = card.locator("[data-av-review-form]")
        assert form.locator("[name=note]").input_value() == ""
        form.locator("[name=decision]").select_option(decision)
        form.locator("[name=review_ack]").check()
        form.get_by_role("button", name="Сохранить подтверждение врача").click()
        expect(page.locator(f"[data-direction={key}]")).to_have_attribute(
            "data-level", "favorable"
        )
        assert (
            page.locator(f"[data-direction={key}]").evaluate(
                "e => getComputedStyle(e).getPropertyValue('--av-tint').trim()"
            )
            == "#ddf2e2"
        )

    dimensions = []
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size(dict(width=width, height=1000))
        panel = page.locator("#av-explanation")
        bounds = panel.bounding_box()
        outer = page.locator("#aviandr").bounding_box()
        assert abs(bounds["width"] - outer["width"]) < 2
        assert panel.evaluate("e => !e.closest('.aviandr-insights-layout')")
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        dimensions.append(
            dict(
                viewport=width,
                explanation_width=bounds["width"],
                analysis_width=outer["width"],
            )
        )
        if width in (390, 1440):
            page.set_viewport_size(dict(width=width, height=1600))
            panel.evaluate(
                "e => window.scrollTo(0, scrollY + e.getBoundingClientRect().top - 100)"
            )
            panel.screenshot(path=str(output / f"explanation-{width}.png"))
    page.locator(".aviandr-directions").screenshot(
        path=str(output / "confirmed-cards-1440.png")
    )

    axe_path = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    violations = None
    if axe_path.exists():
        page.add_script_tag(path=str(axe_path))
        violations = page.evaluate(
            "async () => (await axe.run({include: [['#av-explanation'], ['.aviandr-directions']]}, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}})).violations"
        )
        assert not violations, violations
    assert not errors, errors
    (output / "browser.json").write_text(
        json.dumps(
            dict(
                synthetic_database=True,
                markdown_emphasis_lists_table=True,
                unsafe_html_inert=True,
                four_confirmations_without_note=True,
                immediate_yellow_to_green=True,
                full_width=dimensions,
                javascript_errors=errors,
                axe_violations=violations,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
