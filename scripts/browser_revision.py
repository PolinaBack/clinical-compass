"""Acceptance for the 23 October cabinet changes on disposable, synthetic data."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_revision(patient, doctor, url):
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
    doctor.goto(url + "/doctor/resources")
    expect(doctor.locator("[data-resource-card]")).to_have_count(12)
    doctor.get_by_text("Добавить свой ресурс", exact=True).click()
    form = doctor.locator('form[action="/doctor/resources"]')
    form.locator('[name="title"]').fill("Синтетический ресурс для проверки")
    form.locator('[name="url"]').fill("https://example.org/test-article")
    form.get_by_role("button").click()
    card = doctor.locator("[data-resource-card]").filter(
        has_text="Синтетический ресурс для проверки"
    )
    card.get_by_text("Назначить пациенту", exact=True).click()
    card.locator('[name="patient_id"]').select_option("1")
    card.locator('[name="note"]').fill("Обсудим на следующей встрече")
    card.get_by_role("button", name="Назначить чтение", exact=True).click()
    patient.goto(url + "/me/materials")
    assigned = patient.locator("article").filter(
        has_text="Синтетический ресурс для проверки"
    )
    expect(assigned).to_contain_text("Обсудим на следующей встрече")
    assigned.get_by_role("button", name="Прочитано", exact=True).click()
    expect(
        patient.locator("article").filter(has_text="Синтетический ресурс")
    ).to_contain_text("Прочитано")
    doctor.reload()
    expect(doctor.locator("main")).to_contain_text("Прочитано")
    doctor.locator("[data-resource-search]").fill("дыхание")
    expect(doctor.locator("[data-resource-card]:visible")).to_have_count(1)
    patient.goto(url + "/patients/1/profile/family")
    patient.goto(url + "/patients/1/profile/family/member/mother")
    patient.get_by_label("Имя или родство", exact=True).fill("Синтетическая мама")
    patient.get_by_label("Возраст · если известен", exact=True).fill("54")
    patient.locator('[name="sex"]').select_option("female")
    patient.get_by_label("Что хотите рассказать о близком", exact=True).fill(
        "Заметка на листе"
    )
    patient.get_by_role("button", name="Сохранить сведения", exact=True).click()
    for relation, name, category in [
        ("brother", "Синтетический брат", ""),
        ("sister", "Синтетическая сестра", ""),
        ("custom", "Синтетическая тётя", "Тётя"),
    ]:
        patient.goto(url + "/patients/1/profile/family")
        patient.locator("#family-add-leaf").click()
        f = patient.locator("#family-add-dialog form")
        f.locator('[name="name"]').fill(name)
        f.locator('[name="relation"]').select_option(relation)
        if category:
            f.locator('[name="custom_relation"]').fill(category)
        expect(f.locator('[name="relation"] option[value="mother"]')).to_have_count(0)
        f.get_by_role("button").click()
        expect(
            patient.locator(".family-node-leaf").filter(has_text=name)
        ).to_have_count(1)
    expect(patient.locator('.family-node-leaf[href$="/mother"]')).to_contain_text(
        "Заметка на листе"
    )
    expect(patient.locator('.family-node-leaf[href$="/mother"]')).to_contain_text(
        "54 лет"
    )
    doctor.goto(url + "/patients/1/profile/family")
    expect(
        doctor.get_by_role("heading", name="Медицинская родословная", exact=True)
    ).to_be_visible()
    expect(doctor.locator(".pedigree-node")).to_have_count(10)
    expect(doctor.locator(".proband-arrow")).to_have_count(1)
    expect(
        doctor.locator(".pedigree-node").filter(has_text="Синтетическая тётя")
    ).to_contain_text("Поколение не уточнено")
    assert doctor.locator(".family-connectors").evaluate(
        "e=>e.getBoundingClientRect().height===e.parentElement.getBoundingClientRect().height"
    )
    # A delayed first autosave must never overwrite the current measurements.
    patient.goto(url + "/patients/1/profile/about/personal")
    patient.locator('[name="a_birth_date"]').fill("1990-01-01")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    patient.goto(url + "/patients/1/profile/about/measurements")
    patient.locator('[name="a_height"]').fill("116")
    patient.locator('[name="a_weight"]').fill("75")

    def delay(route):
        response = route.fetch()
        patient.evaluate(
            """() => setTimeout(() => {
            const input=document.querySelector('[name="a_height"]');
            input.value='175';input.dispatchEvent(new Event('input',{bubbles:true}));
        },100)"""
        )
        patient.wait_for_timeout(1000)
        expect(patient.locator("[data-intake-bmi]")).to_have_text("обновляется…")
        route.fulfill(response=response)
        patient.unroute("**/intake/sections/measurements", delay)

    patient.route("**/intake/sections/measurements", delay)
    patient.wait_for_timeout(750)
    patient.locator('[name="a_height"]').fill("175")
    expect(patient.locator("[data-intake-save-status]")).to_contain_text(
        "Черновик сохранён", timeout=20000
    )
    expect(patient.locator("[data-intake-bmi]")).to_have_text("24.5")
    expect(patient.locator("[data-intake-bmi-inputs]")).to_contain_text("175 см")
    patient.reload()
    expect(patient.locator("[data-intake-bmi]")).to_have_text("24.5")
    audit = []
    axe = os.getenv("COMPASS_AXE_PATH")
    Path("artifacts").mkdir(exist_ok=True)
    for page, role, paths in [
        (
            patient,
            "patient",
            [
                "/patients/1/profile/family",
                "/patients/1/profile/about/measurements",
                "/me/materials",
                "/settings",
            ],
        ),
        (
            doctor,
            "doctor",
            ["/patients/1/profile/family", "/doctor/resources", "/settings"],
        ),
    ]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.emulate_media(reduced_motion="reduce")
            for path in paths:
                assert page.goto(url + path).status == 200
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (role, path, width)
                expect(page.locator('a[href="/help-now"]')).to_have_count(
                    1 if role == "patient" else 0
                )
                assert page.locator(".portal-nav").evaluate(
                    'e=>e.closest("header")!==null'
                )
                assert page.locator('a[href*="/reports"]').count() == 0
                page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                nav = page.locator(".portal-nav").bounding_box()
                assert nav["y"] >= 0 and nav["y"] + nav["height"] < 900
                if role == "patient":
                    help_box = page.locator('a[href="/help-now"]').bounding_box()
                    assert (
                        0 <= help_box["y"] < 100
                        and help_box["x"] + help_box["width"] > width - 110
                    )
                page.evaluate("window.scrollTo(0,0)")
                if axe and width in (390, 1280):
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"
                    )
                    audit.append(
                        dict(role=role, path=path, width=width, violations=violations)
                    )
                    Path("artifacts/revision-accessibility.json").write_text(
                        json.dumps(audit, ensure_ascii=False, indent=2)
                    )
                if width in (390, 1280) and path not in ["/settings"]:
                    page.screenshot(
                        path=f'artifacts/revision-{role}-{path.split("/")[-1]}-{width}.png',
                        full_page=True,
                    )
    assert not errors, errors
    assert not any(a["violations"] for a in audit), audit
    print(
        "PASS revision: real article create/assign/read, search, tree leaves and medical symbols, BMI175/75 after delayed autosave and reload, sticky navigation/emergency, four widths and accessibility."
    )
