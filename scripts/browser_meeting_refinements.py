"""Browser checks against disposable recordings supplied by the test runner."""

import json
from pathlib import Path


def check_meeting_refinements(page, base_url, mid, tid, output):
    from playwright.sync_api import expect

    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    url = f"{base_url}/doctor/calendar?patient_id=1&meeting_id={mid}#meeting-workspace"
    page.goto(url)
    records = page.locator("[data-meeting-recording]")
    expect(records).to_have_count(2)
    assert records.evaluate_all("nodes => nodes.every(node => !node.open)")
    expect(records.nth(0).locator(":scope > summary")).to_contain_text("Запись 1")
    expect(records.nth(1).locator(":scope > summary")).to_contain_text("Запись 2")
    page.locator(".meeting-page").screenshot(path=str(out / "recording-list-1440.png"))
    record = records.first
    record.locator(":scope > summary").click()
    expect(record.locator(".meeting-brief")).to_contain_text("Обсудили сон.")
    assert record.locator(".meeting-brief").inner_text().count("Обсудили сон.") == 1
    assert (
        page.get_by_text("Скачать доступные текстовые результаты", exact=False).count()
        == 0
    )
    assert page.request.get(base_url + "/meeting-recordings/1/export").status == 404

    transcript = record.locator("[data-transcript-search]")
    transcript.locator(":scope > summary").click()
    tools = transcript.locator("[data-transcript-tools]")
    query = tools.locator("[data-transcript-query]")
    status = tools.locator("[data-transcript-results]")
    texts = transcript.locator("textarea[data-transcript-text]")
    original = texts.evaluate_all("nodes => nodes.map(node => node.value)")
    query.fill("СОН  СТАЛ  ЛУЧШЕ")
    expect(status).to_contain_text("Совпадение 1 из 2")
    tools.locator("[data-transcript-next]").click()
    expect(status).to_contain_text("Совпадение 2 из 2")
    assert texts.nth(1).evaluate("node => node === document.activeElement")
    assert (
        texts.nth(1).evaluate(
            "node => node.value.slice(node.selectionStart, node.selectionEnd)"
        )
        == "Сон стал лучше"
    )
    tools.locator("[data-transcript-prev]").click()
    expect(status).to_contain_text("Совпадение 1 из 2")
    assert texts.first.evaluate("node => node === document.activeElement")

    query.fill("переход к следующему вопросу")
    expect(status).to_contain_text("Совпадение 1 из 1")
    query.press("Enter")
    expect(transcript.locator(".meeting-search-hit")).to_have_count(2)
    query.fill("<script>window.__transcriptInjected=1</script>")
    expect(status).to_contain_text("Совпадение 1 из 1")
    assert not page.evaluate("Boolean(window.__transcriptInjected)")
    assert tools.locator("script").count() == 0
    query.fill("Нет такой фразы")
    expect(status).to_have_text("Совпадений не найдено.")
    expect(tools.locator("[data-transcript-next]")).to_be_disabled()
    query.fill("сон стал лучше")

    widths = [320, 390, 768, 1440]
    for width in widths:
        page.set_viewport_size(dict(width=width, height=1200))
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        ), width
        if width in (390, 1440):
            tools.screenshot(path=str(out / f"phrase-search-{width}.png"))

    texts.first.fill(original[0].replace("Сон стал лучше.", "Сон стал иначе."))
    expect(status).to_contain_text("Совпадение 1 из 1")
    tools.locator("[data-transcript-clear]").click()
    assert texts.first.input_value().endswith("Переход к")
    assert "Сон стал иначе." in texts.first.input_value()
    assert texts.nth(1).input_value() == original[1]

    page.goto(base_url + f"/meeting-artifacts/{tid}")
    history = page.locator("[data-transcript-search]")
    history.locator("[data-transcript-query]").fill("сон стал лучше")
    expect(history.locator("[data-transcript-results]")).to_contain_text(
        "Совпадение 1 из 2"
    )
    expect(history.locator("[data-transcript-text] mark")).to_have_count(1)
    page.goto(base_url + f"/meetings/{mid}#segment-1-2")
    expect(page.locator("#recording-1")).to_have_attribute("open", "")
    expect(page.locator("#recording-1 .meeting-transcript")).to_have_attribute(
        "open", ""
    )

    axe_path = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    violations = None
    if axe_path.exists():
        page.add_script_tag(path=str(axe_path))
        violations = page.evaluate(
            "async () => (await axe.run({include:[['.meeting-page']]}, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations"
        )
        assert not violations, violations
    assert not errors, errors
    (out / "browser.json").write_text(
        json.dumps(
            dict(
                synthetic_database=True,
                recordings_initially_collapsed=True,
                unique_legacy_summary=True,
                export_removed=True,
                phrase_case_and_whitespace=True,
                phrase_across_segments=True,
                next_previous_and_selection=True,
                search_tracks_unsaved_edits=True,
                search_preserves_text=True,
                history_search=True,
                hash_opens_recording=True,
                unsafe_query_inert=True,
                widths=widths,
                javascript_errors=errors,
                axe_violations=violations,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
