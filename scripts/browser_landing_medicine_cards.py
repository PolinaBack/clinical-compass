"""Visual entry choice and partial-card lifecycle using a synthetic site response."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_landing_medicine_cards(page, url):
    output = Path("artifacts/landing-medicine-cards")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    audits = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.context.clear_cookies()
    page.goto(url + "/")
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        expect(page.locator(".landing-hero")).to_be_visible()
        expect(page.get_by_role("link", name="Я пациент", exact=True)).to_be_visible()
        expect(
            page.get_by_role("link", name="Я специалист", exact=True)
        ).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            page.screenshot(path=str(output / f"landing-{width}.png"))
    page.get_by_role("link", name="Я специалист", exact=True).click()
    form = page.locator('form[action="/login"]')
    form.locator('[name="username"]').fill("doctor")
    form.locator('[name="password"]').fill(os.environ["COMPASS_TEST_PASSWORD"])
    form.get_by_role("button").click()
    page.goto(url + "/medicines/new")
    fetch = page.locator('form[action="/medicines/new/fetch"]')
    fetch.locator('[name="source_url"]').fill("https://example.org/partial")
    fetch.get_by_role("button").click()
    form = page.locator("[data-new-medicine]")
    expect(form.locator('[name="name"]')).to_have_value("Пример")
    expect(form.locator('[name="inn"]')).to_have_value("")
    form.get_by_role("button", name="Сохранить карточку", exact=True).click()
    expect(page.locator("main h1")).to_have_text("Пример")
    code_path = page.url.removeprefix(url)
    expect(page.locator("#medicine-resource a")).to_have_attribute(
        "href", "https://example.org/partial"
    )
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            page.locator("#medicine-resource").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"resource-{width}.png"))
            page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = page.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, violations=violations))
            assert not violations, audits[-1]
    page.locator("#medicine-resource").get_by_text(
        "Удалить лекарство из библиотеки", exact=True
    ).click()
    page.locator("#medicine-resource").get_by_role(
        "button", name="Удалить из библиотеки", exact=True
    ).click()
    expect(page.locator("main")).to_contain_text("Лекарство удалено из библиотеки.")
    expect(page.locator('a[href="' + code_path + '"]')).to_have_count(0)
    page.goto(url + code_path)
    expect(page.locator("main")).to_contain_text("Удалено из библиотеки.")
    expect(page.locator("#medicine-resource a")).to_have_attribute(
        "href", "https://example.org/partial"
    )
    page.get_by_role("button", name="Выйти из кабинета").click()
    page.get_by_role("link", name="Я пациент", exact=True).click()
    form = page.locator('form[action="/login"]')
    form.locator('[name="username"]').fill("patient1")
    form.locator('[name="password"]').fill(os.environ["COMPASS_TEST_PASSWORD"])
    form.get_by_role("button").click()
    assert page.goto(url + "/").status == 200
    assert page.url.rstrip("/") == url
    expect(page.get_by_role("link", name="Я пациент", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Я специалист", exact=True)).to_be_visible()
    assert not errors, errors
    (output / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS: pictured landing and role choice at five widths; partial site parsing with one-word name and no INN; save; visible Resource link; removal from catalogue; archived history/source retained; patient session also opens landing without redirect; two axe A/AA audits; no JS errors."
    )
