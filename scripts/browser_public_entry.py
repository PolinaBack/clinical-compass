"""Check legacy public appearance and current cabinets on synthetic accounts."""

import os
from pathlib import Path
from playwright.sync_api import expect


def check_public_entry(page, url):
    output = Path("artifacts/public-entry-restore")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for path, name in (
        ("/", "home"),
        ("/login/patient", "patient"),
        ("/login/specialist", "specialist"),
    ):
        for width in (320, 390, 768, 1280, 1920):
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(url + path).status == 200
            expect(page.locator("body")).to_have_class("public-entry patient-space")
            expect(page.locator('link[href*="/interface.css"]')).to_have_count(0)
            assert (
                page.evaluate(
                    "getComputedStyle(document.body).getPropertyValue('--canvas').trim()"
                )
                == "#fbfaf8"
            )
            assert "Georgia" in page.locator("h1").evaluate(
                "node=>getComputedStyle(node).fontFamily"
            )
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                path,
                width,
            )
            if width in (390, 1280):
                page.screenshot(path=str(output / f"{name}-{width}.png"))
    form = page.locator('form[action="/login"]')
    form.locator('[name="username"]').fill("doctor")
    form.locator('[name="password"]').fill("invalid-test-password")
    form.get_by_role("button").click()
    expect(page.locator('[role="alert"]')).to_contain_text("Неверные данные")
    expect(page.locator("body")).to_have_class("public-entry patient-space")
    form = page.locator('form[action="/login"]')
    form.locator('[name="username"]').fill("doctor")
    form.locator('[name="password"]').fill(os.environ["COMPASS_TEST_PASSWORD"])
    form.get_by_role("button").click()
    expect(page.locator('nav[aria-label="Навигация специалиста"]')).to_be_visible()
    page.goto(url + "/doctor/library")
    expect(page.locator(".library-entry-grid > .card")).to_have_count(3)
    expect(page.locator("body.portal-shell.signed-in.specialist-space")).to_have_count(
        1
    )
    page.goto(url + "/medicines")
    expect(page.locator(".medicine-add-entry")).to_be_visible()
    page.goto(url + "/")
    expect(page.locator("body")).to_have_class("public-entry patient-space")
    page.get_by_role("button", name="Выйти из кабинета").click()
    expect(
        page.locator('a[href="/login/patient"]').filter(has_text="Я пациент")
    ).to_be_visible()
    assert not errors, errors
    print(
        "PASS: restored public palette, typography and entry layout on all three pages at five widths; no overflow or JS errors; invalid-password form and successful login; current library and medicine creation remain available; logout returns to restored home."
    )
