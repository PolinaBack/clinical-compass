"""Acceptance on disposable fictional cards; never run against a real installation."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_adverse_reactions(doctor, patient, url):
    out = Path("artifacts/adverse-reactions")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    for page in [doctor, patient]:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.emulate_media(reduced_motion="reduce")
    doctor.goto(url + "/doctor/library")
    expect(doctor.locator(".library-section")).to_have_count(4)
    doctor.get_by_role("link", name="Побочные эффекты", exact=False).click()
    form = doctor.locator("[data-adverse-filters]")
    form.locator('[name="drug"]').select_option("D01")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(21)
    expect(doctor.locator("[data-adverse-count]")).to_contain_text("21 запись из 468")
    form.locator('[name="system"]').select_option("ЖКТ")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(3)
    form.locator('[name="drug"]').select_option("D11")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(5)
    form.locator('[name="system"]').select_option("")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(31)
    form.locator('[name="drug"]').select_option("")
    form.locator('[name="q"]').fill("тошнота")
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(12)
    doctor.reload()
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(12)
    doctor.locator("[data-adverse-row]:visible [data-adverse-card]").first.click()
    expect(doctor.locator("#medicine-registry")).to_be_in_viewport()
    doctor.get_by_role(
        "link", name="← Вернуться к выбранному разделу", exact=True
    ).click()
    expect(doctor.locator("[data-adverse-row]:visible")).to_have_count(12)
    form.locator('[name="q"]').fill("неттакогосимптома")
    expect(doctor.locator("[data-adverse-empty]")).to_be_visible()

    patient.goto(url + "/patients/1/profile/about/medicines")
    answer = patient.locator("#intake-answer-form")
    for code in ("S014", "S055", "S072"):
        answer.locator(f'[name="a_avoid_reactions"][value="{code}"]').check()
    fourth = answer.locator('[name="a_avoid_reactions"][value="S115"]')
    fourth.click()
    expect(fourth).not_to_be_checked()
    expect(patient.locator("[data-tolerability-status]")).to_contain_text(
        "максимум три"
    )
    answer.locator('[name="a_primary_reaction"]').select_option("S014")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён"
    )
    doctor.goto(url + "/medicines/prescriptions?patient_id=1&add=D01")
    expect(doctor.locator(".medicine-preferences")).not_to_contain_text("★ Тошнота")
    patient.get_by_role(
        "link", name="Проверить заполненное и отправить врачу", exact=True
    ).click()
    patient.get_by_role("button", name="Отправить анкету врачу", exact=True).click()
    doctor.reload()
    expect(doctor.locator(".medicine-preferences")).to_contain_text("★ Тошнота")
    expect(doctor.locator('[data-preference-matches="D01"]')).to_contain_text(
        "★ Тошнота"
    )
    doctor.locator('[name="D01.dose"]').fill("ВЫМЫШЛЕННАЯ ДОЗА")
    doctor.locator('[name="D01.frequency"]').select_option("1 раз в день")
    doctor.get_by_role("button", name="Сохранить учебный черновик", exact=True).click()
    doctor.wait_for_url("**patient_id=1&saved=1")
    doctor.reload()
    expect(doctor.locator('[data-preference-matches="D01"]')).to_contain_text(
        "★ Тошнота"
    )
    doctor.goto(url + "/medicines/prescriptions?patient_id=2&add=D01")
    expect(doctor.locator(".medicine-preferences")).not_to_contain_text("★ Тошнота")
    doctor.goto(url + "/medicines/d09")
    toc = doctor.locator(".medicine-toc")
    toc.get_by_role("link", name="Фармакодинамика", exact=True).click()
    expect(
        toc.get_by_role("link", name="Фармакодинамика", exact=True)
    ).to_have_attribute("aria-current", "location")
    expect(doctor.locator("#medicine-pharmacodynamics")).to_be_in_viewport()
    doctor.locator("#medicine-contraindications").evaluate(
        "el=>window.scrollTo(0,scrollY+el.getBoundingClientRect().top-125)"
    )
    expect(
        toc.get_by_role("link", name="Противопоказания", exact=True)
    ).to_have_attribute("aria-current", "location")
    assert patient.goto(url + "/medicines/adverse-effects").status == 403

    paths = [
        ("library", "/doctor/library"),
        ("aviandr", "/medicines/adverse-effects?drug=D01"),
        ("spitomin", "/medicines/adverse-effects?drug=D11"),
        ("all", "/medicines/adverse-effects"),
        ("draft", "/medicines/prescriptions?patient_id=1"),
        ("card", "/medicines/d09"),
        ("resources", "/doctor/resources"),
        ("tools", "/clinical-tools"),
    ]
    for width in (320, 390, 768, 1280, 1920):
        doctor.set_viewport_size(dict(width=width, height=1000))
        for name, path in paths:
            assert doctor.goto(url + path).status == 200
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), (width, path)
            assert "Клинический_компас_v11" not in doctor.locator("main").inner_text()
            if width in (390, 1280):
                doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = doctor.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                )
                audits.append(dict(width=width, page=name, violations=violations))
                (out / "accessibility.json").write_text(
                    json.dumps(audits, ensure_ascii=False, indent=2)
                )
                assert not violations, audits[-1]
                doctor.evaluate("window.scrollTo(0,0)")
                if name in ("aviandr", "spitomin", "library", "draft", "tools", "card"):
                    doctor.screenshot(
                        path=str(out / f"{name}-{width}.png"),
                        full_page=name == "library",
                    )
    for width in (390, 1280):
        patient.set_viewport_size(dict(width=width, height=1000))
        assert patient.goto(url + "/patients/1/profile/about/medicines").status == 200
        assert patient.evaluate("document.documentElement.scrollWidth<=innerWidth")
        patient.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
        violations = patient.evaluate(
            """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
        )
        audits.append(
            dict(width=width, page="patient-preferences", violations=violations)
        )
        (out / "accessibility.json").write_text(
            json.dumps(audits, ensure_ascii=False, indent=2)
        )
        assert not violations, audits[-1]
        patient.locator("#medicines-avoid_reactions").evaluate(
            "el=>window.scrollTo(0,scrollY+el.getBoundingClientRect().top-130)"
        )
        patient.screenshot(path=str(out / f"preferences-{width}.png"))
    assert not errors, errors
    print(
        "PASS: 468/15 registry, combined filters/search/reload/card/back, empty result; three preferences and primary, fourth blocked, private draft stays private, sent priorities and exact matches retained per card; saved draft; active TOC by click/scroll; mint library at 5 widths, 18 axe A/AA audits including patient preferences, no overflow or JS errors. Fictional temporary DB.",
        flush=True,
    )
