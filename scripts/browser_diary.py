"""Real physician assignment and accessible diary entry on disposable data."""

from playwright.sync_api import expect


def check_diary(patient, doctor, url):
    doctor.goto(url + "/patients/1/diary")
    doctor.get_by_text("Быстро добавить простой показатель", exact=True).click()
    doctor.get_by_label("Название показателя", exact=True).fill(
        "Энергия · браузерная проверка"
    )
    doctor.get_by_role(
        "button", name="Добавить показатель пациенту", exact=True
    ).click()
    patient.goto(url + "/patients/1/diary")
    assert patient.locator(".portal-nav a").count() == 6
    assert patient.get_by_role("link", name="Главная", exact=True).count() == 0
    wellbeing = patient.locator("#wellbeing-dialog")
    trigger = patient.locator('[data-open="wellbeing-dialog"]')
    trigger.click()
    patient.get_by_label("Самочувствие, 0–10", exact=True).fill("7")
    patient.keyboard.press("Escape")
    assert not wellbeing.evaluate("e=>e.open")
    assert patient.locator("#wellbeing-value").input_value() == ""
    expect(trigger).to_be_focused()
    trigger.click()
    compass = patient.locator(".wellbeing-compass")
    rect = compass.bounding_box()
    # Actual pointer input at the zero mark, followed by keyboard alternatives.
    compass.click(position=dict(x=rect["width"] * 0.19, y=rect["height"] * 0.81))
    assert patient.locator("#wellbeing-value").input_value() == "0"
    slider = patient.get_by_label("Выбрать самочувствие на компасе", exact=True)
    slider.focus()
    patient.keyboard.press("End")
    assert patient.locator("#wellbeing-value").input_value() == "10"
    patient.keyboard.press("Home")
    assert patient.locator("#wellbeing-value").input_value() == "0"
    patient.get_by_role("button", name="Применить оценку", exact=True).click()
    patient.locator('[data-open="sleep-dialog"]').click()
    patient.emulate_media(reduced_motion="no-preference")
    assert (
        patient.locator(".sleep-breath").evaluate(
            "e=>getComputedStyle(e).animationName"
        )
        == "sleep-breath"
    )
    patient.emulate_media(reduced_motion="reduce")
    assert (
        patient.locator(".sleep-breath").evaluate(
            "e=>getComputedStyle(e).animationName"
        )
        == "none"
    )
    patient.get_by_label("Оценка качества сна, 0–10", exact=True).fill("10")
    patient.get_by_label("Продолжительность сна, часы", exact=True).fill("8.5")
    patient.get_by_role("button", name="Применить сон", exact=True).click()
    patient.locator('[data-open="treatment-dialog"]').click()
    patient.get_by_label("Выполнение назначения врача", exact=True).select_option(
        "missed"
    )
    patient.get_by_role("button", name="Применить отметку", exact=True).click()
    patient.locator(".diary-card").filter(
        has_text="Энергия · браузерная проверка"
    ).click()
    patient.get_by_role(
        "spinbutton", name="Энергия · браузерная проверка", exact=True
    ).fill("9")
    patient.get_by_role("button", name="Применить показатель", exact=True).click()
    patient.get_by_label("Заметка", exact=True).fill("Браузер: компас и сон")
    patient.get_by_role("button", name="Сохранить запись", exact=True).click()
    expect(patient.locator("#diary-history")).to_contain_text("качество 10/10")
    expect(patient.locator("#diary-history")).to_contain_text(
        "Энергия · браузерная проверка: 9"
    )
    doctor.reload()
    expect(doctor.locator("#diary-history")).to_contain_text("Браузер: компас и сон")
    expect(doctor.locator("#diary-history")).to_contain_text("Сообщён пропуск")

    import json
    import os
    from pathlib import Path

    audits = []
    axe_path = os.environ.get("COMPASS_AXE_PATH")
    for width in (320, 390):
        patient.set_viewport_size(dict(width=width, height=844))
        for name in ("wellbeing", "sleep"):
            patient.locator(f'[data-open="{name}-dialog"]').click()
            dialog = patient.locator(f"#{name}-dialog")
            assert dialog.evaluate(
                "e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.height<=innerHeight}"
            )
            for _ in range(8):
                patient.keyboard.press("Tab")
                assert dialog.evaluate("e=>e.contains(document.activeElement)")
            if axe_path:
                patient.add_script_tag(path=axe_path)
                violations = patient.evaluate(
                    """async()=> (await axe.run(document,{
                    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
                })).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                )
                audits.append(dict(dialog=name, width=width, violations=violations))
                Path("artifacts/diary-modal-accessibility.json").write_text(
                    json.dumps(audits, ensure_ascii=False, indent=2)
                )
            patient.screenshot(path=f"artifacts/diary-{name}-{width}.png")
            patient.keyboard.press("Escape")
    patient.set_viewport_size(dict(width=1280, height=900))
    assert not any(a["violations"] for a in audits), audits
    no_js = patient.context.browser.new_context(
        storage_state=patient.context.storage_state(), java_script_enabled=False
    )
    try:
        page = no_js.new_page()
        page.goto(url + "/patients/1/diary")
        page.get_by_label("Самочувствие, 0–10", exact=True).fill("6")
        page.get_by_label("Продолжительность сна, часы", exact=True).fill("7")
        page.get_by_label("Заметка", exact=True).fill("Без JavaScript")
        page.get_by_role("button", name="Сохранить запись", exact=True).click()
        expect(page.locator("#diary-history")).to_contain_text("Без JavaScript")
    except Exception:
        page.screenshot(path="artifacts/diary-no-js-failure.png", full_page=True)
        raise
    finally:
        no_js.close()
    print(
        "PASS diary: physician-only metric, pointer compass 0/10, keyboard, Escape, sleep animation/reduced motion, persisted answers, doctor view and no-JS fallback"
    )
