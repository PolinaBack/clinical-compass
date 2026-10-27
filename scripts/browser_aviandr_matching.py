"""Real browser check against a separately provisioned, synthetic local server."""

import json
import time
from pathlib import Path
from playwright.sync_api import expect, Error


def check_aviandr_matching(page, base_url, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    errors, navigations = [], []
    screenshot_style = "body > header { visibility: hidden !important; }"

    def screenshot(selector, name):
        # A successful save can coincide with one legitimate live refresh.
        # Re-resolve a detached element; do not disable the application's poll.
        for attempt in range(3):
            try:
                page.locator(selector).screenshot(
                    path=str(output / name), style=screenshot_style
                )
                return
            except Error as error:
                if attempt == 2 or "not attached" not in str(error):
                    raise

    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "framenavigated",
        lambda frame: (
            navigations.append(frame.url) if frame == page.main_frame else None
        ),
    )
    page.goto(base_url + "/patients/1/ai-analysis")
    page.get_by_role(
        "button", name="Сопоставить с инструкцией через ИИ", exact=True
    ).click()
    expect(
        page.locator("#av-indications [data-av-comparison] strong").first
    ).to_have_text("Сопоставление", timeout=30000)
    expect(page.locator("#av-indications")).to_have_attribute("data-level", "attention")
    screenshot(".aviandr-directions", "yellow-1440.png")

    # A separate dirty form must survive saving the first card.
    other = page.locator("#av-interactions .aviandr-review")
    other.locator(":scope > summary").click()
    other.locator("[name=note]").fill("Несохранённая заметка в другой карточке")
    held_polls = []
    held_posts = []
    poll_url = "**/api/patients/1/ai-analysis/aviandr/current**"
    review_url = "**/patients/1/ai-analysis/aviandr/review*"

    def hold_review(route):
        held_posts.append(route)

    def hold_poll(route):
        response = route.fetch(
            url=base_url + "/api/patients/1/ai-analysis/aviandr/current?checksum="
        )
        held_polls.append((route, response))

    for key, decision in [
        ("indications", "gad"),
        ("contraindications", "complete"),
        ("interactions", "confirmed"),
        ("tolerability", "no_effects"),
    ]:
        card = page.locator(f"#av-{key}")
        expect(card).to_have_attribute("data-level", "attention")
        assert (
            card.evaluate("e => getComputedStyle(e).backgroundColor")
            == "rgb(255, 241, 201)"
        )
        details = card.locator(".aviandr-review")
        if not details.evaluate("e => e.open"):
            details.locator(":scope > summary").click()
        form = details.locator("form")
        form.locator("[name=decision]").select_option(decision)
        form.locator("[name=note]").fill("")
        form.locator("[name=review_ack]").check()
        if key == "tolerability":
            page.route(review_url, hold_review)
        before = len(navigations)
        form.get_by_role(
            "button", name="Сохранить подтверждение врача", exact=True
        ).click()
        if key == "tolerability":
            deadline = time.monotonic() + 12
            while not held_posts and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert held_posts, "Expected the in-flight confirmation request"
            page.route(poll_url, hold_poll)
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            deadline = time.monotonic() + 12
            while not held_polls and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert held_polls, "Expected a poll started during the confirmation POST"
            for route in held_posts:
                route.continue_()
            page.unroute(review_url, hold_review)
        expect(card).to_have_attribute("data-level", "favorable", timeout=10000)
        if key == "tolerability":
            for route, response in held_polls:
                route.fulfill(response=response)
            page.unroute(poll_url, hold_poll)
            page.wait_for_timeout(150)
            expect(card).to_have_attribute("data-level", "favorable")
        assert len(navigations) == before, "Confirmation should not reload the page"
        expect(card).to_have_css("background-color", "rgb(221, 242, 226)")
        if key == "indications":
            expect(other.locator("[name=note]")).to_have_value(
                "Несохранённая заметка в другой карточке"
            )
    screenshot(".aviandr-directions", "green-1440.png")

    dimensions = []
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size(dict(width=width, height=1000))
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        )
        dimensions.append(width)
        if width == 390:
            screenshot("#av-contraindications", "green-390.png")
    axe_path = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    violations = None
    if axe_path.exists():
        page.add_script_tag(path=str(axe_path))
        violations = page.evaluate(
            "async () => (await axe.run({include: [['.aviandr-directions']]}, {runOnly: {type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations"
        )
        assert not violations, violations

    # A free description not covered by the exact alias matcher is found by
    # the transport fixture for the model, through the real queue and polling.
    fact_form = page.locator('form[action$="/aviandr/facts"]')
    fact_form.locator("..").locator(":scope > summary").click()
    fact_form.locator("[name=kind]").select_option("factor")
    fact_form.locator("[name=key]").fill("Эпилепсия с редкими приступами")
    fact_form.locator("[name=state]").select_option("present")
    fact_form.locator("[name=note]").fill(
        "Синтетическая запись для проверки смыслового сопоставления."
    )
    fact_form.get_by_role("button", name="Сохранить сведение", exact=True).click()
    page.get_by_role(
        "button", name="Сопоставить с инструкцией через ИИ", exact=True
    ).click()
    red = page.locator("#av-contraindications")
    expect(red).to_have_attribute("data-level", "critical", timeout=30000)
    assert (
        red.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(251, 224, 228)"
    )
    expect(red.locator("[data-av-comparison]")).to_contain_text(
        "Эпилепсия с редкими приступами"
    )
    red.locator(".aviandr-review > summary").click()
    red.locator("[name=decision]").select_option("complete")
    red.locator("[name=review_ack]").check()
    red.get_by_role("button", name="Сохранить подтверждение врача", exact=True).click()
    expect(red.locator(".aviandr-review-current").first).to_contain_text("Проверено")
    expect(red).to_have_attribute("data-level", "critical")
    screenshot("#av-contraindications", "red-1440.png")
    assert not errors, errors
    (output / "browser.json").write_text(
        json.dumps(
            dict(
                synthetic_only=True,
                model="httpx transport fixture, not a real model accuracy evaluation",
                actual_queue_and_polling=True,
                semantic_indication=True,
                semantic_contraindication=True,
                four_ajax_confirmations_without_notes=True,
                no_navigation_on_confirmation=True,
                delayed_old_poll_cannot_revert_confirmation=True,
                actual_background_colours=True,
                separate_unsaved_form_preserved=True,
                real_risk_survives_confirmation=True,
                responsive_widths=dimensions,
                javascript_errors=errors,
                axe_violations=violations,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
