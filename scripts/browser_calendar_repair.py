"""Open every editor mode, including error/back navigation, on fictional accounts."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect


def check_calendar_repair(doctor, patient, url):
    out = Path("artifacts/calendar-repair")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    doctor.on("pageerror", lambda error: errors.append(str(error)))
    doctor.emulate_media(reduced_motion="reduce")
    original = (
        url
        + "/doctor/calendar?week=2030-01-07&view=day&patient_id=2&timezone=Europe%2FMoscow"
    )
    doctor.goto(original)
    doctor.locator('[data-calendar-add="free"]').click()
    form = doctor.locator("#calendar-event-form")
    form.locator('[name="time"]').select_option("09:00")
    form.locator('[name="end_date"]').fill("2030-01-07")
    form.locator('[name="end_time"]').fill("17:00")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".event-open")).to_contain_text("09:00–17:00")
    doctor.goto(original)
    doctor.locator('[data-calendar-add="appointment"]').click()
    form.locator('[name="time"]').select_option("10:00")
    form.locator('[name="end_time"]').fill("12:00")
    form.locator('[name="url"]').fill("https://example.org/fictional-room")
    form.locator('[name="fallback"]').fill("Вымышленный резервный контакт")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.get_by_role("heading", name="Действие не выполнено")).to_be_visible()
    link = doctor.locator("[data-error-back]")
    target = link.get_attribute("href")
    params = parse_qs(urlsplit(target).query)
    assert urlsplit(target).path == "/doctor/calendar"
    assert (
        params["patient_id"] == ["2"]
        and params["view"] == ["day"]
        and params["week"] == ["2030-01-07"]
    )
    doctor.screenshot(path=str(out / "conflict-before-back.png"), full_page=True)
    link.click()
    expect(form).to_be_visible()
    expect(form.locator('[name="patient_id"]')).to_have_value("2")
    expect(form.locator('[name="time"]')).to_have_value("10:00")
    expect(form.locator('[name="end_time"]')).to_have_value("12:00")
    doctor.screenshot(path=str(out / "restored-editor.png"), full_page=True)
    form.locator('[name="time"]').select_option("18:00")
    form.locator('[name="end_date"]').fill("2030-01-10")
    form.locator('[name="end_time"]').fill("21:30")
    # A fresh GET may be used by the back link; re-enter connection fields only
    # if the browser did not retain them. No secret fields are stored in URLs.
    form.locator('[name="url"]').fill("https://example.org/fictional-room")
    form.locator('[name="fallback"]').fill("Вымышленный резервный контакт")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".calendar-visit")).to_contain_text(
        "07.01.2030 18:00 — 10.01.2030 21:30"
    )

    for width in (320, 390, 768, 1280, 1920):
        for mode in ("free", "appointment", "past"):
            doctor.set_viewport_size(dict(width=width, height=1000))
            doctor.goto(url + "/doctor/calendar?week=2030-01-07&view=day&patient_id=2")
            doctor.locator(
                "[data-calendar-past]"
                if mode == "past"
                else f'[data-calendar-add="{mode}"]'
            ).click()
            expect(form).to_be_visible()
            expect(form.locator('[name="end_date"]')).to_be_visible()
            expect(form.locator('[name="ends_next_day"]')).to_have_count(0)
            assert (
                max(
                    doctor.locator(".calendar-form-grid>label:visible").evaluate_all(
                        "els=>els.map(el=>el.getBoundingClientRect().height)"
                    )
                )
                < 120
            ), (width, mode)
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (width, mode)
            doctor.locator(".calendar-visit summary").click()
            check = doctor.locator(".calendar-visit label.check").first
            assert (
                check.locator("input").evaluate("el=>el.getBoundingClientRect().width")
                == 18
            )
            assert (
                check.locator("span").evaluate("el=>el.getBoundingClientRect().width")
                > 100
            )
            assert check.evaluate("el=>el.getBoundingClientRect().height") < 80
            doctor.evaluate("window.scrollTo(0,0)")
            doctor.screenshot(path=str(out / f"{mode}-{width}.png"), full_page=True)
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, mode=mode, violations=violations))
            assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS: open free/appointment/past editors at 320/390/768/1280/1920; normal date fields and 18px checkboxes, no vertical letter columns or page overflow; 8-hour window and multi-day appointment saved; conflict -> Back restores same calendar/day/patient/editor times; 15 axe A/AA audits, no JS errors. Fictional temporary DB, no models.",
        flush=True,
    )
