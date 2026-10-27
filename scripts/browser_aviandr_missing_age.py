"""The screenshot case: confirmed history, no numeric age or birth date."""

import json
from pathlib import Path
from playwright.sync_api import expect


def check_aviandr_missing_age(page, base_url, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url + "/patients/1/ai-analysis")
    card = page.locator("#av-contraindications")
    card.locator(".aviandr-review > summary").click()
    card.locator("[name=review_ack]").check()
    card.get_by_role("button", name="Сохранить подтверждение врача", exact=True).click()
    expect(card.locator(".aviandr-status")).to_have_text(
        "Анамнез подтверждён — остались уточнения"
    )
    expect(card.locator("[data-conclusion]")).not_to_contain_text(
        "Пациент не предоставил"
    )
    expect(card.locator(".aviandr-unknown")).to_contain_text("Возраст не указан")
    expect(card).to_have_css("background-color", "rgb(255, 241, 201)")
    card.locator(".aviandr-review > summary").click()
    card.locator("[name=adult_confirmed]").check()
    card.locator("[name=review_ack]").check()
    card.get_by_role("button", name="Сохранить подтверждение врача", exact=True).click()
    expect(card).to_have_attribute("data-level", "favorable")
    expect(card).to_have_css("background-color", "rgb(221, 242, 226)")
    expect(card.locator("[data-conclusion]")).to_contain_text("18+ подтверждено врачом")
    expect(card.locator("[data-conclusion]")).not_to_contain_text("Возраст не уточнён")
    page.reload()
    expect(card).to_have_attribute("data-level", "favorable")
    expect(card.locator("[name=adult_confirmed]")).to_be_checked()
    for width in [390, 1440]:
        page.set_viewport_size(dict(width=width, height=1000))
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        card.screenshot(
            path=str(output / f"confirmed-{width}.png"),
            style="body > header { visibility:hidden !important; }",
        )
    axe = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    violations = None
    if axe.exists():
        page.add_script_tag(path=str(axe))
        violations = page.evaluate(
            "async () => (await axe.run({include:[['#av-contraindications']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations"
        )
        assert not violations, violations
    assert not errors, errors
    (output / "browser.json").write_text(
        json.dumps(
            dict(
                synthetic_only=True,
                no_initial_age_or_birth_date=True,
                confirmed_history_explains_remaining_age_gap=True,
                inline_adult_confirmation_immediately_green=True,
                survives_reload=True,
                responsive_widths=[390, 1440],
                javascript_errors=errors,
                axe_violations=violations,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
