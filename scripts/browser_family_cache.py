"""Tree regressions with a real warm HTTP cache, not a fresh browser only."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_family_cache(patient, doctor, url):
    """The caller primes old /static/intake.css with a real max-age response."""
    artifacts = Path("artifacts/family-cache")
    artifacts.mkdir(parents=True, exist_ok=True)
    css_failures = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: css_failures.append(str(error)))
    patient.goto(url + "/patients/1/profile/family")
    patient.goto(url + "/patients/1/profile/family/member/mother")
    patient.get_by_label("Имя или родство", exact=True).fill("Учебная мама")
    patient.get_by_label("Возраст · если известен", exact=True).fill("54")
    patient.get_by_label("Что хотите рассказать о близком", exact=True).fill(
        "Учебная заметка на листе"
    )
    patient.get_by_role("button", name="Сохранить сведения", exact=True).click()
    for relation, name in [("brother", "Учебный брат"), ("sister", "Учебная сестра")]:
        patient.goto(url + "/patients/1/profile/family")
        patient.locator("#family-add-leaf").click()
        form = patient.locator("#family-add-dialog form")
        form.locator('[name="name"]').fill(name)
        form.locator('[name="relation"]').select_option(relation)
        form.get_by_role("button").click()
    # Reload normally: the old unversioned stylesheet must stay in HTTP cache.
    patient.goto(url + "/__cache_primer")
    patient.reload()
    expect(patient.locator("body")).to_contain_text("Старая версия")
    stats = patient.request.get(url + "/__cache_stats").json()
    assert stats["legacy_requests"] == 1, stats
    audits = []
    for page, role in [(patient, "patient"), (doctor, "doctor")]:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            assert page.goto(url + "/patients/1/profile/family").status == 200
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            # Actual loaded dimensions and computed styles catch the screenshot defect.
            diagram = page.locator(".family-diagram")
            connectors = page.locator(
                ".family-connectors" if role == "doctor" else ".tree-art"
            )
            assert connectors.evaluate("e=>getComputedStyle(e).position") == "absolute"
            assert connectors.evaluate(
                "e=>e.getBoundingClientRect().height"
            ) == diagram.evaluate("e=>e.getBoundingClientRect().height")
            assert (
                connectors.locator(
                    "path" if role == "doctor" else ".tree-branches path"
                ).first.evaluate("e=>getComputedStyle(e).fill")
                == "none"
            )
            assert (
                connectors.locator(
                    "path" if role == "doctor" else ".tree-branches path"
                ).first.evaluate("e=>getComputedStyle(e).stroke")
                != "none"
            )
            nodes = page.locator(
                ".family-node-leaf" if role == "patient" else ".pedigree-node"
            )
            expect(nodes).to_have_count(9)
            assert nodes.evaluate_all(
                'els=>els.every(e=>getComputedStyle(e).position==="absolute")'
            )
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
            expect(page.locator("#family")).to_contain_text("Учебная мама")
            if role == "patient":
                expect(
                    page.locator('.family-node-leaf[href$="/mother"]')
                ).to_contain_text("Учебная заметка на листе")
            if width in (390, 1280):
                page.screenshot(
                    path=str(artifacts / f"{role}-{width}.png"), full_page=True
                )
                axe = os.getenv("COMPASS_AXE_PATH")
                if axe:
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        "async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"
                    )
                    audits.append(dict(role=role, width=width, violations=violations))
    assert patient.request.get(url + "/__cache_stats").json()["legacy_requests"] == 1
    # The drawing must not borrow its required styles from the intake stylesheet.
    patient.route("**/static/intake.css*", lambda route: route.abort())
    patient.goto(url + "/patients/1/profile/family")
    assert (
        patient.locator(".family-node-leaf").first.evaluate(
            "e=>getComputedStyle(e).position"
        )
        == "absolute"
    )
    patient.unroute("**/static/intake.css*")
    # If the tree CSS fails, HTML still provides one readable list item per person.
    patient.route("**/static/family.css*", lambda route: route.abort())
    patient.goto(url + "/patients/1/profile/family")
    expect(patient.locator(".family-node-list > li")).to_have_count(9)
    assert (
        patient.locator(".tree-art").evaluate("e=>e.getBoundingClientRect().height")
        == 0
    )
    assert (
        patient.locator(".family-diagram").evaluate(
            "e=>e.getBoundingClientRect().height"
        )
        < 650
    )
    ys = patient.locator(".family-node-list > li").evaluate_all(
        "els=>els.map(e=>e.getBoundingClientRect().top)"
    )
    assert all(a < b for a, b in zip(ys, ys[1:]))
    expect(patient.locator('.family-node-leaf[href$="/mother"]')).to_contain_text(
        "Учебная заметка на листе"
    )
    patient.screenshot(
        path=str(artifacts / "readable-without-family-css.png"), full_page=True
    )
    patient.unroute("**/static/family.css*")
    (artifacts / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    assert not any(a["violations"] for a in audits), audits
    assert not css_failures, css_failures
    print(
        "PASS family cache: real cached old CSS, normal reload, patient and doctor, 320/390/768/1280px, no overlapping nodes, real SVG stroke, independent stylesheet, readable fallback when CSS is unavailable, 4 axe audits."
    )
