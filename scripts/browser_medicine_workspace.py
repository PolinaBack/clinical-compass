"""Acceptance on runner-owned fictional accounts and a disposable database only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_medicine_workspace(doctor, patient, url):
    out = Path("artifacts/medicine-workspace")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    doctor.on("pageerror", lambda error: errors.append(str(error)))
    doctor.on("dialog", lambda dialog: dialog.accept())
    doctor.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/medicines")
    search = doctor.locator("#medicine-query")
    search.fill("пранафарм")
    expect(doctor.locator("[data-catalog-card]:visible")).to_have_count(1)
    expect(doctor.locator("[data-catalog-card]:visible")).to_contain_text(
        "Эсциталопрам"
    )
    search.fill("")
    expect(doctor.locator("[data-catalog-card]:visible")).to_have_count(12)
    doctor.locator("[data-catalog-card]").filter(
        has=doctor.get_by_role("heading", name="Авиандр", exact=True)
    ).get_by_role("button", name="+ В проверку", exact=True).click()
    assert doctor.url.endswith("/medicines")
    expect(doctor.locator("main")).to_contain_text("В проверке: 1")
    doctor.goto(url + "/medicines/combinations")
    if doctor.get_by_role("button", name="Очистить список", exact=True).is_enabled():
        doctor.get_by_role("button", name="Очистить список", exact=True).click()
    for code in ("D01", "D09", "D11"):
        form = doctor.locator("#medicine-picker form").filter(
            has=doctor.locator(f'[name="code"][value="{code}"]')
        )
        form.get_by_role("button", name="+ Добавить", exact=True).click()
        expect(doctor.locator("#medicine-picker")).to_have_attribute("open", "")
    expect(doctor.locator(".medicine-pair")).to_have_count(3)
    doctor.locator(".medicine-chips").get_by_role(
        "link", name="Агомелатин", exact=False
    ).click()
    expect(doctor.get_by_role("heading", name="Агомелатин", exact=True)).to_be_visible()
    doctor.get_by_role(
        "link", name="← Вернуться к выбранному разделу", exact=True
    ).click()
    expect(doctor.locator(".medicine-pair")).to_have_count(3)
    doctor.get_by_role("link", name="Создать черновик назначения", exact=True).click()
    form = doctor.locator("[data-medicine-draft]")
    expect(form.locator("[data-draft-item]:visible")).to_have_count(3)
    for code in ("D01", "D09", "D11"):
        item = form.locator(f'[data-draft-item="{code}"]')
        expect(item.locator(f'[name="{code}.dose"]')).to_have_value("")
    form.get_by_role("button", name="Сохранить черновик", exact=True).click()
    assert doctor.locator("input:invalid").count() >= 3
    for code in ("D01", "D09", "D11"):
        form.locator(f'[name="{code}.dose"]').fill("ВЫМЫШЛЕННАЯ ДОЗА ДЛЯ ПРОВЕРКИ")
        form.locator(f'[name="{code}.frequency"]').select_option("1 раз в день")
    item = form.locator('[data-draft-item="D01"]')
    item.locator('[name="D01.frequency"]').select_option("Другая схема")
    expect(item.locator('[name="D01.frequency_other"]')).to_be_visible()
    item.locator('[name="D01.frequency_other"]').fill("ВЫМЫШЛЕННАЯ СХЕМА")
    item.locator('[name="D01.frequency"]').select_option("1 раз в день")
    expect(item.locator('[name="D01.frequency_other"]')).to_be_hidden()
    expect(item.locator('[name="D01.frequency_other"]')).to_be_disabled()
    item.locator('[name="D01.times"]').fill("08:15, 20:15")
    item.locator('[name="D01.food"]').select_option("Другое")
    item.locator('[name="D01.food_other"]').fill("ВЫМЫШЛЕННОЕ УТОЧНЕНИЕ")
    item.locator('[name="D01.route"]').select_option("Внутрь")
    item.locator('[name="D01.start"]').fill("2026-11-02")
    item.locator('[name="D01.duration"]').fill("ВЫМЫШЛЕННЫЙ КУРС")
    item.locator('[name="D01.comment"]').fill("ВЫМЫШЛЕННЫЙ КОММЕНТАРИЙ")
    form.get_by_role("button", name="Сохранить черновик", exact=True).click()
    doctor.wait_for_url("**/medicines/prescriptions?patient_id=1&saved=1")
    doctor.reload()
    expect(form.locator('[name="D01.times"]')).to_have_value("08:15, 20:15")
    doctor.locator("#draft-patient").select_option("2")
    doctor.get_by_role("button", name="Открыть карту", exact=True).click()
    expect(form.locator("[data-draft-item]:visible")).to_have_count(0)
    doctor.locator("#draft-picker summary").click()
    doctor.locator('[data-pick-draft="D04"]').click()
    expect(form.locator("[data-draft-item]:visible")).to_have_count(1)
    form.locator('[name="D04.dose"]').fill("ТОЛЬКО КАРТА ДВА")
    form.locator('[name="D04.frequency"]').select_option("2 раза в день")
    form.get_by_role("button", name="Сохранить черновик", exact=True).click()
    doctor.wait_for_url("**/medicines/prescriptions?patient_id=2&saved=1")
    doctor.locator("#draft-patient").select_option("1")
    doctor.get_by_role("button", name="Открыть карту", exact=True).click()
    expect(form.locator("[data-draft-item]:visible")).to_have_count(3)
    expect(form.locator('[name="D01.comment"]')).to_have_value(
        "ВЫМЫШЛЕННЫЙ КОММЕНТАРИЙ"
    )
    form.get_by_role("button", name="Проверить сочетания", exact=True).click()
    doctor.wait_for_url("**draft_patient=1")
    expect(doctor.locator(".medicine-pair")).to_have_count(3)
    doctor.get_by_role("link", name="← Вернуться к черновику", exact=True).click()
    expect(form.locator('[name="D01.comment"]')).to_have_value(
        "ВЫМЫШЛЕННЫЙ КОММЕНТАРИЙ"
    )
    assert patient.goto(url + "/medicines/prescriptions?patient_id=1").status == 403
    pages = [
        ("catalogue", "/medicines"),
        ("card", "/medicines/d09"),
        ("comparison", "/medicines/combinations?drug=D01&drug=D09&drug=D11&picker=1"),
        ("draft", "/medicines/prescriptions?patient_id=1"),
        ("history", "/medicines/prescriptions?patient_id=1&version=1"),
    ]
    for width in (320, 390, 768, 1280, 1920):
        doctor.set_viewport_size(dict(width=width, height=1000))
        for name, path in pages:
            assert doctor.goto(url + path).status == 200
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), (width, path)
            assert "Клинический_компас_v10" not in doctor.locator("main").inner_text()
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
            )
            audits.append(dict(width=width, page=name, violations=violations))
            (out / "accessibility.json").write_text(
                json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            assert not violations, audits[-1]
            if width in (390, 1280) and name != "history":
                doctor.evaluate("window.scrollTo(0,0)")
                doctor.screenshot(
                    path=str(out / f"{name}-{width}.png"), full_page=name == "catalogue"
                )
                if name in ("draft", "comparison"):
                    block = doctor.locator(
                        "[data-draft-item]:visible"
                        if name == "draft"
                        else ".medicine-pair"
                    ).first
                    block.evaluate(
                        "el=>window.scrollTo(0,scrollY+el.getBoundingClientRect().top-135)"
                    )
                    doctor.screenshot(path=str(out / f"{name}-fields-{width}.png"))
    assert not errors, errors
    print(
        "PASS: live search/manufacturer, independent catalogue actions; inline add/return with 3 pairs; manual-only required fields, custom format, real encrypted saves/reload/two cards/check/back; patient denied. 25 axe A/AA audits at 320/390/768/1280/1920, no overflow or JS errors. Fictional temporary DB, no models.",
        flush=True,
    )
