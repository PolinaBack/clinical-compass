"""Real calendar booking, cancelled materials, collapsed history and one-row header."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import expect


def check_consultations(patient, doctor, url):
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
    doctor.goto(url + "/doctor/calendar")
    form = doctor.locator('form[action="/doctor/calendar/slots"]')
    form.locator('[name="starts_at"]').fill(
        (datetime.now() + timedelta(days=12))
        .replace(hour=10, minute=0)
        .isoformat(timespec="minutes")
    )
    form.locator('[name="url"]').fill("https://example.org/consultation")
    form.locator('[name="fallback"]').fill("Согласованный телефон")
    form.get_by_role("button", name="Опубликовать свободное окно", exact=True).click()
    patient.goto(url + "/patients/1/profile/about")
    patient.locator(".profile-nav").get_by_role(
        "link", name="Консультации и видеосвязь"
    ).click()
    assert patient.url.endswith("/patients/1/profile/video")
    expect(
        patient.get_by_role("heading", name="Личный профиль", exact=True)
    ).to_be_visible()
    expect(patient.locator(".profile-nav")).to_be_visible()
    expect(
        patient.get_by_text("Записаться или изменить время", exact=True)
    ).to_have_count(0)
    form = patient.locator('form[action="/patients/1/consultations"]')
    sid = form.locator("option").nth(1).get_attribute("value")
    form.locator('[name="slot_id"]').select_option(sid)
    form.get_by_role("button", name="Записаться на консультацию", exact=True).click()
    expect(patient.locator("#video")).to_contain_text("Время подтверждено")
    expect(patient.locator(".profile-nav")).to_be_visible()
    expect(patient.locator(f'[name="slot_id"] option[value="{sid}"]')).to_have_count(0)
    doctor.goto(url + "/patients/1/profile/video")
    expect(doctor.locator("#video")).to_contain_text("Согласованный телефон")
    article = doctor.locator("#video article").filter(has_text="Согласованный телефон")
    article.get_by_text("Подготовка, завершение или отмена приёма", exact=True).click()
    article.get_by_role("button", name="Отменить приём", exact=True).click()
    patient.reload()
    expect(patient.locator(f'[name="slot_id"] option[value="{sid}"]')).to_have_count(1)
    # Direct links preserve old entries while opening their collapsed parent.
    patient.goto(url + "/patients/1/diary")
    for text in ("Первая заметка для проверки", "Последняя заметка для проверки"):
        patient.locator('form[action="/patients/1/notes"] textarea').fill(text)
        patient.locator('form[action="/patients/1/notes"] button').click()
    expect(patient.locator("#notes")).to_contain_text("Последняя заметка для проверки")
    expect(
        patient.get_by_text("Первая заметка для проверки", exact=True)
    ).not_to_be_visible()
    patient.get_by_text("Ещё заметки (1)", exact=True).click()
    expect(
        patient.get_by_text("Первая заметка для проверки", exact=True)
    ).to_be_visible()
    for index in (1, 2):
        # The no-JS fields are an equally supported form; use its real controls.
        patient.locator('[data-open="wellbeing-dialog"]').click()
        patient.get_by_label("Самочувствие, 0–10", exact=True).fill(str(5 + index))
        patient.get_by_role("button", name="Применить оценку", exact=True).click()
        patient.locator('[data-open="sleep-dialog"]').click()
        patient.get_by_label("Оценка качества сна, 0–10", exact=True).fill("7")
        patient.get_by_label("Продолжительность сна, часы", exact=True).fill("8")
        patient.get_by_role("button", name="Применить сон", exact=True).click()
        patient.get_by_label("Заметка", exact=True).fill(f"Запись для проверки {index}")
        patient.get_by_role("button", name="Сохранить запись", exact=True).click()
    expect(patient.get_by_text("Запись для проверки 1", exact=True)).not_to_be_visible()
    expect(patient.get_by_text("Запись для проверки 2", exact=True)).to_be_visible()
    patient.locator("#diary-older summary").click()
    expect(patient.get_by_text("Запись для проверки 1", exact=True)).to_be_visible()
    old_id = patient.locator("#diary-older article").first.get_attribute("id")
    patient.goto(url + "/patients/1/diary#" + old_id)
    expect(patient.locator("#" + old_id)).to_be_visible()
    # Cancellation through the doctor's interface removes active patient actions.
    doctor.goto(url + "/doctor/resources")
    card = doctor.locator("[data-resource-card]").first
    title = card.locator("h2").inner_text().replace(" ↗", "")
    card.get_by_text("Назначить пациенту", exact=True).click()
    card.locator('[name="patient_id"]').select_option("1")
    card.get_by_role("button", name="Назначить чтение", exact=True).click()
    patient.goto(url + "/me/materials")
    expect(patient.get_by_role("heading", name=title, exact=True)).to_be_visible()
    expect(patient.get_by_text("О приложении", exact=True)).to_have_count(0)
    assigned = doctor.locator("#assignments article").filter(has_text=title).first
    assigned.get_by_role("button", name="Отменить назначение", exact=True).click()
    patient.reload()
    expect(patient.get_by_role("heading", name=title, exact=True)).to_have_count(0)
    audits = []
    axe = os.getenv("COMPASS_AXE_PATH")
    Path("artifacts").mkdir(exist_ok=True)
    for page, role, paths in [
        (
            patient,
            "patient",
            ["/patients/1/diary", "/patients/1/profile/video", "/settings"],
        ),
        (
            doctor,
            "doctor",
            ["/doctor/calendar", "/patients/1/profile/video", "/doctor/resources"],
        ),
    ]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            for path in paths:
                assert page.goto(url + path).status == 200
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (role, path, width)
                expect(page.locator(".header-tools")).to_have_count(0)
                if role == "patient":
                    header = page.locator(".header-inner").bounding_box()
                    help_box = page.locator('a[href="/help-now"]').bounding_box()
                    exit_box = page.locator(
                        'header form[action="/logout"] button'
                    ).bounding_box()
                    nav_box = page.locator(".portal-nav").bounding_box()
                    centers = [
                        b["y"] + b["height"] / 2 for b in (help_box, exit_box, nav_box)
                    ]
                    assert max(centers) - min(centers) < 4, (width, centers)
                    assert header["height"] < 100, (width, header)
                    if width <= 600:
                        assert page.locator(".portal-nav a").evaluate_all(
                            "els=>els.every(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})"
                        )
                    assert (
                        help_box["x"] > nav_box["x"] and exit_box["x"] > help_box["x"]
                    )
                    page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                    assert (
                        0
                        <= page.locator('a[href="/help-now"]').bounding_box()["y"]
                        < 90
                    )
                    page.evaluate("window.scrollTo(0,0)")
                    # Keyboard focus scrolls every navigation item into its own row.
                    page.locator(".portal-nav a").first.focus()
                    for _ in range(5):
                        page.keyboard.press("Tab")
                    assert page.locator("a.settings-link").evaluate(
                        "e=>e===document.activeElement"
                    )
                    assert page.locator("a.settings-link").evaluate(
                        "e=>{const r=e.getBoundingClientRect(),n=e.parentElement.getBoundingClientRect();return r.left>=n.left-1&&r.right<=n.right+1}"
                    )
                else:
                    expect(page.locator('a[href="/help-now"]')).to_have_count(0)
                    expect(
                        page.get_by_role("link", name="Риски и решения", exact=True)
                    ).to_have_count(0)
                if axe and width in (390, 1280):
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"
                    )
                    audits.append(
                        dict(role=role, path=path, width=width, violations=violations)
                    )
                    Path("artifacts/consultations-accessibility.json").write_text(
                        json.dumps(audits, ensure_ascii=False, indent=2)
                    )
                if width in (390, 1280) and path in (
                    "/patients/1/diary",
                    "/patients/1/profile/video",
                    "/doctor/calendar",
                ):
                    page.locator(".portal-nav").evaluate("e=>e.scrollLeft=0")
                    page.screenshot(
                        path=f'artifacts/consultations-{role}-{path.split("/")[-1]}-{width}.png',
                        full_page=True,
                    )
    assert not errors, errors
    assert not any(a["violations"] for a in audits), audits
    patient.set_viewport_size(dict(width=320, height=900))
    patient.goto(url + "/settings")
    preference = patient.locator('form[action="/settings/interface"]')
    preference.locator('[name="minimal"]').check()
    preference.get_by_role("button").click()
    assert patient.locator(".portal-nav .category-icon").evaluate_all(
        "els=>els.length===6&&els.every(e=>getComputedStyle(e).display!=='none')"
    )
    preference = patient.locator('form[action="/settings/interface"]')
    preference.locator('[name="minimal"]').uncheck()
    preference.get_by_role("button").click()
    # User-requested patient logout really invalidates access to the cabinet.
    patient.locator('header form[action="/logout"] button').click()
    assert patient.request.get(url + "/patients/1/diary").status == 401
    print(
        "PASS consultations: actual slot publish/book/cancel/release, shared profile tabs, note/diary latest+history+deep-link, material cancellation, one-row sticky header/logout, both roles at four widths, keyboard and accessibility."
    )
