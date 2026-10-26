"""Library navigation and real doctor-created cards, using synthetic input only."""

import json
import os
import re
from pathlib import Path
from playwright.sync_api import expect


def check_library_refresh(page, url):
    out = Path("artifacts/library-refresh")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    audits = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.emulate_media(reduced_motion="reduce")
    page.goto(url + "/doctor/forums")
    forum_color = page.locator(".forum-entry-grid > .card").first.evaluate(
        "node=>getComputedStyle(node).backgroundColor"
    )
    page.goto(url + "/doctor/library")
    expect(page.locator(".library-entry-grid > .card")).to_have_count(3)
    expect(page.locator('main a[href="/medicines/adverse-effects"]')).to_have_count(0)
    assert (
        page.locator(".library-entry-grid > .card").first.evaluate(
            "node=>getComputedStyle(node).backgroundColor"
        )
        == forum_color
    )
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            page.screenshot(path=str(out / f"library-{width}.png"))
    for path in (
        "/medicines",
        "/medicines/adverse-effects",
        "/medicines/combinations",
        "/clinical-tools",
        "/doctor/resources",
    ):
        assert page.goto(url + path).status == 200
        expect(page.locator("main .library-back a")).to_have_attribute(
            "href", "/doctor/library"
        )
        assert page.locator("main .library-back").evaluate(
            "node=>Boolean(node.compareDocumentPosition(document.querySelector('main h1'))&Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        expect(page.locator("main")).not_to_contain_text("Импорт и источники")
        expect(page.locator("main")).not_to_contain_text("Сценарии с Авиандром")
    page.goto(url + "/medicines/new")
    form = page.locator("[data-new-medicine]")
    form.locator('[name="name"]').fill("Синтетическая карточка вручную")
    form.locator('[name="inn"]').fill("Учебное вещество")
    form.locator('[name="manufacturer"]').fill("Вымышленный производитель")
    form.locator("summary").filter(has_text=re.compile("^Показания$")).click()
    form.locator('[name="section_indications"]').fill(
        "Технический пример раздела без медицинского утверждения."
    )
    form.get_by_role("button", name="Сохранить карточку", exact=True).click()
    expect(page.locator("main h1")).to_have_text("Синтетическая карточка вручную")
    expect(page.locator(".medicine-card-heading")).to_contain_text(
        "Вымышленный производитель"
    )
    expect(page.locator("#medicine-indications")).to_contain_text(
        "Технический пример раздела"
    )
    first_path = page.url
    page.goto(url + "/medicines/new")
    fetch = page.locator('form[action="/medicines/new/fetch"]')
    fetch.locator('[name="source_url"]').fill("https://example.org/synthetic-drug")
    fetch.get_by_role("button", name="Получить данные по ссылке", exact=True).click()
    form = page.locator("[data-new-medicine]")
    expect(form.locator('[name="name"]')).to_have_value("Тестовая карточка")
    expect(form.locator('[name="inn"]')).to_have_value("Синтетическое вещество")
    expect(form.locator('[name="section_indications"]')).to_have_value(
        "ТЕХНИЧЕСКАЯ ФИКСТУРА: не медицинское показание."
    )
    form.locator('[name="manufacturer"]').fill("Исправлено вручную после загрузки")
    form.get_by_role("button", name="Сохранить карточку", exact=True).click()
    expect(page.locator(".medicine-card-heading")).to_contain_text(
        "Исправлено вручную после загрузки"
    )
    expect(page.locator("#medicine-indications")).to_contain_text(
        "ТЕХНИЧЕСКАЯ ФИКСТУРА"
    )
    for width in (320, 390, 768, 1280, 1920):
        page.set_viewport_size(dict(width=width, height=1000))
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if width in (390, 1280):
            page.screenshot(path=str(out / f"card-{width}.png"))
            page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = page.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, violations=violations))
            assert not violations, audits[-1]
    page.goto(url + "/medicines?q=Синтетическая")
    expect(page.locator("[data-catalog-card]")).to_have_count(1)
    expect(page.locator("[data-catalog-card] h2 a")).to_have_attribute(
        "href", first_path.removeprefix(url)
    )
    page.goto(url + "/medicines/new")
    fetch = page.locator('form[action="/medicines/new/fetch"]')
    fetch.locator('[name="source_url"]').fill("https://127.0.0.1/private")
    fetch.get_by_role("button", name="Получить данные по ссылке", exact=True).click()
    expect(page.locator('[role="alert"]')).to_contain_text("Локальные и внутренние")
    expect(page.locator("[data-new-medicine]")).to_be_visible()
    for path in ("/medicines/import", "/medicines/import/1", "/medicines/scenarios"):
        assert page.goto(url + path).status == 410
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS: three white library cards; five section back links; manual and synthetic-link creation; editable fetched fields; search; five widths; two axe A/AA audits; private-address rejection; retired pages 410; no JS errors"
    )
