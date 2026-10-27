"""Visible upload, closed-row progress and opening actual results on synthetic media."""

import json
import re
from pathlib import Path


def check_recording_visibility(page, base, mid, wav_path, output):
    from playwright.sync_api import expect

    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    url = f"{base}/doctor/calendar?patient_id=1&meeting_id={mid}#meeting-workspace"
    page.goto(url)
    form = page.locator(f'form[action="/meetings/{mid}/recordings"]')
    form.locator("[name=file]").set_input_files(str(wav_path))
    form.get_by_role("button", name="Загрузить запись", exact=True).click()
    record = page.locator("[data-meeting-recording]").first
    expect(record).to_have_attribute("open", "")
    expect(record.get_by_text("Файл загружен и сохранён.", exact=True)).to_be_visible()
    badge = record.locator("[data-recording-status]")
    expect(badge).to_contain_text("расшифровка не запущена")
    record.get_by_role("button", name="Расшифровать запись", exact=True).click()
    expect(badge).to_contain_text("Расшифровка")
    record.locator(":scope > summary").click()
    expect(badge).to_be_visible()
    expect(badge).to_have_attribute("data-recording-state", "done", timeout=20000)
    record.locator(":scope > summary").click()
    record.get_by_role("link", name="Показать расшифровку", exact=True).click()
    transcript = page.locator(".meeting-transcript")
    expect(transcript).to_have_attribute("open", "")
    expect(transcript.locator("textarea").first).to_be_visible()
    expect(transcript.locator("textarea").first).to_have_value(
        "Синтетический разговор о сне."
    )
    record.get_by_role(
        "button", name="Подготовить итоги и следующие шаги", exact=True
    ).click()
    expect(badge).to_contain_text(re.compile("итог", re.I))
    expect(badge).to_have_attribute("data-recording-state", "done", timeout=20000)
    record.get_by_role("link", name="Показать итоги", exact=True).click()
    expect(
        record.locator(".meeting-brief").get_by_text("Обсуждался сон.", exact=True)
    ).to_be_visible()
    dimensions = []
    for width in [320, 390, 768, 1440]:
        page.set_viewport_size(dict(width=width, height=1600))
        page.goto(url)
        expect(badge).to_be_visible()
        assert record.get_attribute("open") is None
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth + 1"
        ), width
        dimensions.append(width)
        if width in (390, 1440):
            panel = page.locator(".meeting-page")
            panel.evaluate(
                "e => window.scrollTo(0, scrollY + e.getBoundingClientRect().top - 100)"
            )
            panel.screenshot(path=str(out / f"recording-list-{width}.png"))
    page.goto(
        f"{base}/meetings/{mid}?recording=1&recording_view=transcript#transcript-1"
    )
    expect(transcript).to_have_attribute("open", "")
    transcript.evaluate(
        "e => window.scrollTo(0, scrollY + e.getBoundingClientRect().top - 100)"
    )
    transcript.screenshot(path=str(out / "transcript-open-1440.png"))
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
                actual_wav_upload=True,
                new_record_opens=True,
                upload_confirmation_visible=True,
                status_visible_while_collapsed=True,
                actual_worker_updates_badge=True,
                ready_link_opens_transcript=True,
                ready_link_opens_summary=True,
                default_list_stays_collapsed=True,
                widths=dimensions,
                javascript_errors=errors,
                axe_violations=violations,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
