"""Patient leaf garden, any-order additions, editing, no-JS fallback and pedigree."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_family_leaves(patient, doctor, url):
    folder = Path("artifacts/family-leaves")
    folder.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
    tree = url + "/patients/1/profile/family"
    patient.goto(tree)
    expect(patient.locator(".tree-canopy")).to_have_count(1)
    expect(patient.locator(".family-tree-garden")).to_be_visible()
    patient.screenshot(path=str(folder / "empty-1280.png"), full_page=True)
    add = patient.locator("#family-add-dialog")
    edit = patient.locator("#family-edit-dialog")
    patient.locator("#family-add-leaf").click()
    expect(add).to_be_visible()
    expect(add.locator('[name="name"]')).not_to_have_attribute("required", "")
    expect(add.locator('[name="relation"] option[value="sibling"]')).to_have_count(0)
    add.locator('[name="relation"]').select_option("sister")
    add.locator('[name="name"]').fill("Елена")
    add.locator('[name="about"]').fill("Заметка о сестре")
    add.get_by_role("button", name="Добавить участника", exact=True).click()
    sister = patient.locator(".family-node-leaf").filter(has_text="Елена")
    expect(sister).to_be_visible()
    expect(sister).to_contain_text("Сестра")
    for relation, name, category in [
        ("brother", "Антон", ""),
        ("uncle", "Павел", ""),
        ("custom", "Ольга", "Двоюродная сестра"),
    ]:
        patient.locator("#family-add-leaf").click()
        add.locator('[name="relation"]').select_option(relation)
        if category:
            add.locator('[name="custom_relation"]').fill(category)
        add.locator('[name="name"]').fill(name)
        add.get_by_role("button", name="Добавить участника", exact=True).click()
        expect(
            patient.locator(".family-node-leaf").filter(has_text=name)
        ).to_be_visible()
    # A grandmother can be the first person of her branch; no parent has been entered.
    patient.locator('.family-node-leaf[data-family-add="maternal_grandmother"]').click()
    expect(add.locator('[name="relation"]')).to_have_value("maternal_grandmother")
    add.locator('[name="name"]').fill("Мария")
    add.get_by_role("button", name="Добавить участника", exact=True).click()
    expect(patient.locator("#leaf-maternal_grandmother")).to_contain_text("Мария")
    expect(patient.locator("#leaf-mother")).not_to_have_class("leaf-filled")
    expect(patient.locator("#leaf-father")).not_to_have_class("leaf-filled")
    # Edit in place, preserve the same leaf, and reopen its complete history.
    sister.click()
    expect(edit).to_be_visible()
    expect(edit.locator('[name="about"]')).to_have_value("Заметка о сестре")
    edit.locator('[name="about"]').fill("Сестра поддерживает меня")
    edit.get_by_role("button", name="Сохранить сведения", exact=True).click()
    patient.reload()
    expect(sister).to_contain_text("Сестра поддерживает меня")
    custom = patient.locator(".family-node-leaf").filter(has_text="Ольга")
    custom.click()
    expect(edit.locator('[name="custom_relation"]')).to_have_value("Двоюродная сестра")
    edit.locator('[name="custom_relation"]').fill("Крёстная")
    edit.get_by_role("button", name="Сохранить сведения", exact=True).click()
    expect(custom).to_contain_text("Крёстная")
    patient.goto(tree)
    patient.screenshot(path=str(folder / "filled-1280.png"), full_page=True)
    # Links and one-person forms also work with JavaScript disabled.
    no_js = patient.context.browser.new_context(
        storage_state=patient.context.storage_state(), java_script_enabled=False
    )
    try:
        page = no_js.new_page()
        page.goto(tree)
        page.locator("#family-add-leaf").click()
        expect(
            page.get_by_role("heading", name="Добавить близкого", exact=True)
        ).to_be_visible()
        form = page.locator('form[action="/patients/1/relatives"]')
        form.locator('[name="relation"]').select_option("custom")
        form.locator('[name="custom_relation"]').fill("Двоюродный брат")
        form.get_by_role("button", name="Добавить участника", exact=True).click()
        expect(
            page.locator(".family-node-leaf").filter(has_text="Двоюродный брат")
        ).to_be_visible()
        page.reload()
        expect(
            page.locator(".family-node-leaf").filter(has_text="Двоюродный брат")
        ).to_have_count(1)
        page.screenshot(path=str(folder / "no-js.png"), full_page=True)
    finally:
        no_js.close()
    audits = []
    axe = os.getenv("COMPASS_AXE_PATH")
    for page, role in [(patient, "patient"), (doctor, "doctor")]:
        for width in [320, 390, 768, 1280]:
            page.set_viewport_size(dict(width=width, height=900))
            page.goto(tree)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                role,
                width,
            )
            if role == "patient":
                nodes = page.locator(".family-node-leaf")
                expect(page.locator(".family-add-leaf")).to_have_count(1)
                assert (
                    page.locator(".tree-art").evaluate(
                        "e=>getComputedStyle(e).position"
                    )
                    == "absolute"
                )
            else:
                nodes = page.locator(".pedigree-node")
                expect(page.locator(".family-add-leaf")).to_have_count(0)
                expect(
                    page.get_by_role("heading", name="Медицинская родословная")
                ).to_be_visible()
            boxes = nodes.evaluate_all(
                "els=>els.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})"
            )
            for i, a in enumerate(boxes):
                for b in boxes[i + 1 :]:
                    assert not (
                        min(a["x"] + a["w"], b["x"] + b["w"]) > max(a["x"], b["x"]) + 1
                        and min(a["y"] + a["h"], b["y"] + b["h"])
                        > max(a["y"], b["y"]) + 1
                    ), (role, width, a, b)
            if width in [390, 1280]:
                page.screenshot(
                    path=str(folder / f"{role}-{width}.png"), full_page=True
                )
                if axe:
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"
                    )
                    audits.append(dict(role=role, width=width, violations=violations))
            if role == "patient":
                page.locator("#family-add-leaf").click()
                dialog = page.locator("#family-add-dialog")
                assert dialog.evaluate(
                    "e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}"
                )
                for _ in range(8):
                    page.keyboard.press("Tab")
                    assert dialog.evaluate("e=>e.contains(document.activeElement)")
                if width in [390, 1280] and axe:
                    violations = page.evaluate(
                        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"
                    )
                    audits.append(
                        dict(
                            role=role,
                            width=width,
                            page="add-dialog",
                            violations=violations,
                        )
                    )
                if width == 390:
                    page.screenshot(path=str(folder / "add-dialog-390.png"))
                page.keyboard.press("Escape")
                expect(page.locator("#family-add-leaf")).to_be_focused()
    (folder / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    assert not any(a["violations"] for a in audits), audits
    assert not errors, errors
    print(
        "PASS family leaves: historical canopy/branches, empty garden, sister and brother without parents, grandmother and uncle without ancestors, custom category/edit/reload, short leaf dialogs, full history/no-JS fallback, preserved doctor pedigree, both roles at 320/390/768/1280, no overlaps/overflow, keyboard, accessibility."
    )
