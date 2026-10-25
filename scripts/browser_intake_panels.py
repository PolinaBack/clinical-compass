"""Visible anamnesis panels on a caller-owned, disposable synthetic database."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_intake_panels(patient, doctor, url):
    out = Path("artifacts/intake-panels")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))

    def saved(page):
        expect(page.locator("[data-intake-save-status]")).to_contain_text(
            "Черновик сохранён", timeout=20000
        )

    def add(kind, name, related=None):
        patient.goto(
            url
            + f"/patients/1/intake/entities/new/{kind}"
            + (f"?related={related}" if related else "")
        )
        patient.locator('[name="a_name"]').fill(name)
        saved(patient)
        eid = int(patient.locator('[name="entity_id"]').input_value())
        patient.locator("[data-saved-entity-link]").click()
        return eid

    patient.goto(url + "/patients/1/profile/about/medicines")
    expect(
        patient.get_by_role("link", name="Добавить препарат", exact=False)
    ).to_be_visible()
    expect(patient.locator(".intake-section-records")).to_contain_text(
        "Пока нет записей"
    )
    med = add("medication", "Учебный препарат <пример>")
    patient.locator(".intake-extra > summary").click()
    patient.locator('[name="a_frequency"]').fill("Учебная частота")
    saved(patient)
    patient.get_by_role("link", name="✎ Комментарий", exact=True).click()
    expect(patient.locator('[name="a_comment"]')).to_be_focused()
    patient.locator('[name="a_comment"]').fill("Синтетическое описание, не назначение")
    saved(patient)
    patient.goto(url + "/patients/1/profile/about/medicines")
    row = patient.locator('[data-intake-record="medication"]')
    expect(row).to_contain_text("Учебный препарат <пример>")
    expect(row).to_contain_text("Учебная частота")
    row.click()
    expect(patient.locator('[name="a_name"]')).to_have_value(
        "Учебный препарат <пример>"
    )
    patient.locator('[name="a_name"]').fill("Очень длинное учебное название " * 10)
    saved(patient)
    visit = add("visit", "Учебный специалист")
    patient.locator(".intake-extra > summary").click()
    patient.locator('[name="a_date"]').fill("2026-10-01")
    saved(patient)
    condition = add("condition", "Учебное описание жалобы", med)
    expect(patient.locator(".intake-linked-actions a").nth(0)).to_contain_text(
        "1 запись"
    )
    expect(patient.locator(".intake-linked-actions a").nth(1)).to_contain_text(
        "0 записей"
    )
    patient.locator(".intake-linked-actions a").nth(0).focus()
    patient.keyboard.press("Enter")
    expect(
        patient.locator("#intake-linked-medication [data-intake-record]")
    ).to_be_focused()
    expect(
        patient.locator(f'[data-intake-record="medication"][href*="/{med}"]')
    ).to_have_count(1)
    patient.goto(url + "/patients/1/profile/about/health")
    expect(patient.locator(".intake-linked-actions a").nth(0)).to_contain_text(
        "1 запись"
    )
    expect(patient.locator(".intake-linked-actions a").nth(1)).to_contain_text(
        "1 запись"
    )

    patient.goto(url + "/patients/1/profile/about/exams")
    patient.get_by_role("link", name="Файл", exact=True).click()
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF"
    patient.locator("[data-record-file]").set_input_files(
        [
            dict(name="Учебный документ.pdf", mimeType="application/pdf", buffer=pdf),
            dict(name="Проверка удаления.pdf", mimeType="application/pdf", buffer=pdf),
        ]
    )
    patient.get_by_role(
        "button", name="Сохранить выбранные вложения", exact=True
    ).click()
    file_link = patient.get_by_role("link", name="Учебный документ.pdf", exact=True)
    expect(file_link).to_be_visible()
    file_url = file_link.get_attribute("href")
    assert patient.request.get(url + file_url).status == 200
    assert doctor.request.get(url + file_url).status == 403
    removable = patient.locator(".intake-file").filter(has_text="Проверка удаления.pdf")
    removable.locator("summary").click()
    removable.get_by_role("button", name="Удалить файл", exact=True).click()
    expect(
        removable
    ).to_be_visible()  # Required confirmation blocks an accidental click.
    removable.get_by_role("checkbox").check()
    removable.get_by_role("button", name="Удалить файл", exact=True).click()
    expect(
        patient.get_by_role("link", name="Проверка удаления.pdf", exact=True)
    ).to_have_count(0)
    expect(
        patient.get_by_role("link", name="Учебный документ.pdf", exact=True)
    ).to_be_visible()
    patient.get_by_role("link", name="Голосовая заметка", exact=True).click()
    expect(patient.locator("[data-record-start]")).to_be_focused()
    expect(patient.locator("[data-record-stop]")).to_be_hidden()

    patient.goto(url + "/patients/1/profile/about/mental")
    patient.locator('[data-intake-field="mood"]').get_by_role(
        "radio", name="5", exact=True
    ).check()
    saved(patient)
    extras = patient.locator(".intake-extra [data-intake-state]").evaluate_all(
        "els=>Object.fromEntries(els.map(e=>[e.name,e.value]))"
    )
    expect(patient.locator(".intake-empty-panel")).to_have_count(0)
    patient.locator(
        '[data-intake-field="anxiety"] [data-intake-set-state="unknown"]'
    ).click()
    saved(patient)
    patient.reload()
    expect(patient.locator('[name="s_mood"]')).to_have_value("answered")
    expect(patient.locator('[name="a_mood"][value="5"]')).to_be_checked()
    expect(patient.locator('[name="s_anxiety"]')).to_have_value("unknown")
    assert (
        patient.locator(".intake-extra [data-intake-state]").evaluate_all(
            "els=>Object.fromEntries(els.map(e=>[e.name,e.value]))"
        )
        == extras
    )
    patient.locator("[data-intake-next]").click()
    expect(patient).to_have_url(url + "/patients/1/profile/about/health")
    patient.get_by_role("button", name="Предыдущий раздел", exact=False).click()
    expect(patient).to_have_url(url + "/patients/1/profile/about/mental")
    expect(patient.locator('[name="a_mood"][value="5"]')).to_be_checked()

    # External footer buttons still submit a native form without JavaScript.
    plain_context = patient.context.browser.new_context(java_script_enabled=False)
    plain_context.add_cookies(patient.context.cookies())
    plain = plain_context.new_page()
    plain.goto(url + "/patients/1/profile/about/mental")
    plain.locator(".intake-extra > summary").click()
    plain.locator('[name="a_comment"]').fill("Сохранено нижней кнопкой без JavaScript")
    plain.get_by_role("button", name="Сохранить раздел", exact=True).focus()
    plain.keyboard.press("Enter")
    expect(plain.locator('[name="a_comment"]')).to_have_value(
        "Сохранено нижней кнопкой без JavaScript"
    )
    expect(plain.locator("[data-intake-section-state]")).to_have_count(0)
    plain_context.close()

    patient.goto(url + "/patients/1/profile/about")
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    doctor.goto(url + "/patients/1/profile/about/medicines")
    published_href = doctor.locator('[data-intake-record="medication"]').get_attribute(
        "href"
    )
    assert "submission_id=" in published_href
    expect(
        doctor.locator("[data-local-recording], [data-intake-section-state]")
    ).to_have_count(0)
    assert doctor.request.get(url + file_url).status == 200
    patient.goto(url + f"/patients/1/intake/entities/{med}")
    patient.locator('[name="a_name"]').fill("Новый частный черновик")
    saved(patient)
    add("medication", "Ещё одна частная запись")
    doctor.reload()
    expect(doctor.locator("main")).not_to_contain_text("Новый частный черновик")
    expect(doctor.locator('[data-intake-record="medication"]')).to_have_count(1)
    expect(doctor.locator(".intake-linked-actions a").nth(0)).to_contain_text(
        "1 запись"
    )

    audits = []
    routes = [
        (patient, label, path)
        for label, path in [
            ("medicines", "/patients/1/profile/about/medicines"),
            ("exams", "/patients/1/profile/about/exams"),
            ("visits", "/patients/1/profile/about/visits"),
            ("health", "/patients/1/profile/about/health"),
            ("mental", "/patients/1/profile/about/mental"),
            ("entity", f"/patients/1/intake/entities/{condition}"),
        ]
    ] + [
        (doctor, "doctor-medicines", "/patients/1/profile/about/medicines"),
        (doctor, "doctor-entity", f"/patients/1/intake/entities/{condition}"),
    ]
    for page, label, path in routes:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.goto(url + path)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (label, width)
            if width in (390, 1280):
                page.screenshot(path=str(out / f"{label}-{width}.png"), full_page=True)
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(page=label, width=width, violations=violations))
                assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS panels: visible add/list controls, metadata/escaping/edit/reload, real linked counts, unified file upload/download/confirmed deletion, voice link without automatic recording, per-question unknown preserves other answers and closed extras, previous/next/native footer, immutable clinician snapshot and private draft, both roles at four widths; 16 axe checks; no JavaScript errors.",
        flush=True,
    )
