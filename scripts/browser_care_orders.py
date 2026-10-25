"""Run only on disposable fictional accounts using the browser acceptance runner."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_care_orders(doctor, patient, url):
    out = Path("artifacts/care-orders")
    out.mkdir(parents=True, exist_ok=True)
    audits, errors = [], []
    for page in [doctor, patient]:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/patients/1/workspace/assessments")
    expect(
        doctor.get_by_role("link", name="Анкеты и динамика", exact=True)
    ).to_have_attribute("aria-current", "page")
    expect(doctor.locator("#questionnaire-results")).to_be_visible()
    doctor.get_by_role("link", name="Назначения", exact=True).click()
    form = doctor.locator("[data-care-form]")
    assert (
        form.locator("button").evaluate("el=>getComputedStyle(el).backgroundColor")
        == "rgb(50, 107, 80)"
    )
    expect(doctor.locator("[data-care-custom]")).to_be_hidden()
    form.locator('[name="purpose"]').fill(
        "Обсудить результаты на следующей консультации — учебный пример"
    )
    form.locator('[name="preparation"]').fill("Подготовка по указанию специалиста")
    form.get_by_role("button", name="+ Назначить пациенту", exact=True).click()
    expect(doctor.locator('[data-care-order="1"]')).to_contain_text(
        "Общий анализ крови"
    )
    # Changing type changes the actual choices, not just the label.
    form.locator('[name="kind"]').select_option("instrumental")
    expect(form.locator('[name="name"]')).to_have_value("ЭКГ")
    form.locator('[name="name"]').select_option("УЗИ органов брюшной полости")
    form.locator('[name="due"]').select_option("date")
    expect(doctor.locator("[data-care-date]")).to_be_visible()
    form.locator('[name="due_date"]').fill("2026-10-31")
    form.get_by_role("button", name="+ Назначить пациенту", exact=True).click()
    expect(doctor.locator('[data-care-order="2"]')).to_contain_text("До 31.10.2026")
    form.locator('[name="kind"]').select_option("advice")
    expect(doctor.locator("[data-care-custom]")).to_be_visible()
    form.locator('[name="custom_name"]').fill("Свой учебный пункт")
    form.locator('[name="due"]').select_option("other")
    form.locator('[name="due_text"]').fill("После следующей встречи")
    form.get_by_role("button", name="+ Назначить пациенту", exact=True).click()
    expect(doctor.locator('[data-care-order="3"]')).to_contain_text(
        "Свой учебный пункт"
    )

    patient.goto(url + "/patients/1/profile")
    patient.get_by_role(
        "link", name="Мои назначения и обследования", exact=True
    ).click()
    expect(patient.locator("[data-care-order]")).to_have_count(3)
    assert (
        patient.locator('[data-care-order="1"] button.secondary').first.evaluate(
            "el=>getComputedStyle(el).color"
        )
        == "rgb(112, 69, 182)"
    )
    expect(patient.locator('[data-care-order="1"]')).to_contain_text(
        "Подготовка по указанию специалиста"
    )
    order = patient.locator('[data-care-order="1"]')
    order.locator(".care-upload > summary").click()
    order.locator('[type="file"]').set_input_files(
        dict(
            name="synthetic-result.pdf",
            mimeType="application/pdf",
            buffer=b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF",
        )
    )
    order.locator('[name="file_comment"]').fill(
        "Вымышленный результат для проверки интерфейса"
    )
    order.get_by_role("button", name="Прикрепить и отправить", exact=True).click()
    expect(patient.locator('[data-care-order="1"]')).to_contain_text(
        "synthetic-result.pdf"
    )
    patient.reload()
    order.get_by_role("button", name="Отметить выполненным", exact=True).click()
    expect(order).to_contain_text("Отмечено выполненным")
    doctor.reload()
    expect(doctor.locator('[data-care-order="1"]')).to_contain_text(
        "synthetic-result.pdf"
    )
    doctor.locator('[data-care-order="1"] a', has_text="synthetic-result.pdf").click()
    expect(doctor.locator("main")).to_contain_text("Антивирусная проверка не выполнена")
    doctor.get_by_role("link", name="← Вернуться к назначению", exact=True).click()
    order = doctor.locator('[data-care-order="3"]')
    order.locator(".care-cancel > summary").click()
    order.get_by_role("button", name="Подтвердить отмену", exact=True).click()
    expect(doctor.locator(".care-assigned > h3")).to_contain_text("Назначено: 2")
    expect(doctor.locator(".care-history > summary")).to_contain_text(
        "Отменённые назначения"
    )
    patient.reload()
    patient.locator(".care-history > summary").click()
    expect(patient.locator('[data-care-order="3"]')).to_contain_text(
        "Отменено специалистом"
    )
    # A stale/invalid submission returns to the same patient's assignments.
    doctor.locator('[data-care-form] [name="kind"]').select_option("lab")
    doctor.locator('[data-care-form] [name="name"]').evaluate(
        "el=>{const o=new Option('Invalid','invalid',true,true);el.add(o)}"
    )
    doctor.locator("[data-care-form]").get_by_role(
        "button", name="+ Назначить пациенту", exact=True
    ).click()
    expect(doctor.locator("main")).to_contain_text("Выберите тип и исследование")
    doctor.locator("main a", has_text="Назад").click()
    expect(doctor).to_have_url(url + "/patients/1/assignments")
    expect(doctor.locator('[data-care-order="1"]')).to_contain_text(
        "Общий анализ крови"
    )
    doctor.goto(url + "/patients/2/assignments")
    expect(doctor.locator("[data-care-order]")).to_have_count(0)
    doctor.goto(url + "/medicines/adverse-effects?drug=D01")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(0)
    gi = doctor.locator('[data-adverse-system="ЖКТ"]')
    gi.locator("summary").click()
    expect(gi.locator("[data-adverse-row]:visible")).to_have_count(3)
    gi.locator("summary").focus()
    doctor.keyboard.press("Enter")
    expect(gi.locator("[data-adverse-row]:visible")).to_have_count(0)
    doctor.locator('[data-adverse-filters] [name="q"]').fill("тошнота")
    expect(gi.locator("[data-adverse-row]:visible")).to_have_count(1)
    doctor.reload()
    expect(gi.locator("[data-adverse-row]:visible")).to_have_count(1)

    paths = [
        (doctor, "doctor", "/patients/1/assignments"),
        (patient, "patient", "/patients/1/profile/assignments"),
        (doctor, "adverse", "/medicines/adverse-effects?drug=D01"),
        (doctor, "file", "/patients/1/assignments/orders/1/files/1"),
    ]
    for width in (320, 390, 768, 1280, 1920):
        for page, name, path in paths:
            page.set_viewport_size(dict(width=width, height=1000))
            assert page.goto(url + path).status == 200
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                width,
                name,
            )
            if name == "adverse":
                page.locator('[data-adverse-system="ЖКТ"] > summary').click()
            page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
            if width in (390, 1280):
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                )
                audits.append(dict(width=width, page=name, violations=violations))
                (out / "accessibility.json").write_text(
                    json.dumps(audits, ensure_ascii=False, indent=2)
                )
                assert not violations, audits[-1]
                page.screenshot(
                    path=str(out / f"{name}-{width}.png"), full_page=name == "adverse"
                )
                if name in ("patient", "doctor"):
                    page.locator('[data-care-order="1"]').evaluate(
                        "el=>window.scrollTo(0,scrollY+el.getBoundingClientRect().top-130)"
                    )
                    page.screenshot(path=str(out / f"{name}-order-{width}.png"))
    assert not errors, errors
    print(
        "PASS: separate tabs; real sends for lab/instrumental/custom requests, due dates, patient profile, upload and persisted result, completion/cancel/history, same-section error return, other card empty; native adverse disclosures by mouse/keyboard/search/reload; 5 widths without overflow, 8 axe A/AA audits, no console errors. Disposable fictional DB only.",
        flush=True,
    )
