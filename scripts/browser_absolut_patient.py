"""Visible calculated cards for the additive synthetic Абсолют scenario."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_absolut_patient(doctor, url, pid):
    out = Path("artifacts/absolut")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    doctor.on("pageerror", lambda error: errors.append(str(error)))
    doctor.emulate_media(reduced_motion="reduce")
    for width in (320, 390, 768, 1280, 1920):
        doctor.set_viewport_size(dict(width=width, height=1000))
        response = doctor.goto(f"{url}/patients/{pid}/ai-analysis#aviandr")
        # Repeating an identical fragment URL can be a same-document navigation.
        if response is None:
            response = doctor.reload()
        assert response.status == 200
        block = doctor.locator("[data-aviandr-analysis]")
        expect(block.locator(".aviandr-direction")).to_have_count(4)
        expect(block.locator("[data-conclusion]")).to_have_count(4)
        expect(block.locator("#av-indications [data-conclusion]")).to_contain_text(
            "GAD-7: 16 → 7"
        )
        expect(
            block.locator("#av-contraindications [data-conclusion]")
        ).to_contain_text("36")
        expect(block.locator("#av-interactions [data-conclusion]")).to_contain_text(
            "Эсциталопрам"
        )
        expect(block.locator("#av-tolerability [data-conclusion]")).to_contain_text(
            "Тошнота"
        )
        expect(block.locator("[data-trend]")).to_have_count(3)
        expect(block.locator("[data-questionnaire-trend]")).to_have_count(3)
        assert doctor.evaluate(
            "document.documentElement.scrollWidth<=innerWidth"
        ), width
        if width in (390, 1280):
            block.locator(".aviandr-directions").screenshot(
                path=str(out / f"cards-{width}.png")
            )
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(width=width, violations=violations))
            assert not violations, audits[-1]
    for summary in doctor.locator(".aviandr-direction-details > summary").all():
        summary.click()
    expect(doctor.locator("#av-details-tolerability")).to_contain_text(
        "Дней с отметкой приёма"
    )
    expect(doctor.locator("#av-details-tolerability")).to_contain_text(
        "Возможная связь с препаратом"
    )
    doctor.get_by_text("Исходные анкеты и сведения карты", exact=True).click()
    expect(
        doctor.locator(".aviandr-block details details > summary").filter(
            has_text="PHQ4"
        )
    ).to_have_count(3)
    expect(doctor.locator("#av-details-interactions")).to_contain_text("Эсциталопрам")
    source = doctor.locator("#av-indications .aviandr-basis a").first
    source.click()
    expect(doctor.locator("#av-evidence-P-indications-context")).to_be_visible()
    out.joinpath("accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert not errors, errors
    print(
        "PASS: Абсолют; four real calculated cards; 30 diary days, nine forms; sources/details; five widths; two axe A/AA audits; no JS errors"
    )
