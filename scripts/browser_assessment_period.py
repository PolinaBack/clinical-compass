"""Resume old questionnaires on caller-owned disposable synthetic records only.

Supply authenticated patient/doctor pages and PHQ-4, safety6, HCL-33 assessment
IDs with yesterday's reference dates. No working database is accessed here.
"""

import json
import re
import os
from pathlib import Path

from playwright.sync_api import expect


def check_assessment_period(patient, doctor, url, ids, period):
    out = Path("artifacts/assessment-period")
    out.mkdir(parents=True, exist_ok=True)
    errors, failed_saves = [], []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                failed_saves.append((response.url, response.status))
                if "/save" in response.url and response.status >= 400
                else None
            ),
        )
        page.emulate_media(reduced_motion="reduce")

    def choose(item, value):
        patient.locator(f'[name="answer_{item}"][value="{value}"]').check()

    def save(label="Сохранить и продолжить"):
        patient.get_by_role("button", name=label, exact=True).click()
        expect(patient).to_have_url(re.compile(r"[?&]saved=1(?:&|$)"))

    phq4 = f"/assessments/{ids['phq4']}"
    safety = f"/assessments/{ids['safety6']}"
    hcl = f"/assessments/{ids['hcl33']}"
    patient.goto(url + phq4)
    expect(patient.locator("[data-assessment-period-note]")).to_be_visible()
    expect(patient.locator(".assessment-intro")).to_contain_text(period[1])
    expect(patient.locator('[name="period_ack"]')).to_have_count(0)
    choose(1, 2)
    patient.get_by_text("Добавить комментарий или обсудить", exact=True).click()
    patient.get_by_role("textbox", name="Комментарий к вопросу 1", exact=True).fill(
        "Синтетический ответ после паузы"
    )
    patient.get_by_label("Хочу обсудить", exact=False).check()
    save("Сохранить и сделать паузу")
    patient.reload()
    expect(patient.locator('[name="answer_1"][value="2"]')).to_be_checked()
    expect(patient.get_by_label("Хочу обсудить", exact=False)).to_be_checked()
    expect(
        patient.get_by_role("textbox", name="Комментарий к вопросу 1", exact=True)
    ).to_have_value("Синтетический ответ после паузы")
    save()
    choose(2, 1)
    save("Предыдущий вопрос")
    expect(patient.locator('[name="answer_1"][value="2"]')).to_be_checked()
    save()
    expect(patient.locator('[name="answer_2"][value="1"]')).to_be_checked()
    save()
    choose(3, 2)
    save()
    choose(4, 1)
    save("Завершить и отправить")
    expect(patient.locator(".assessment-intro .badge")).to_have_text("Отправлено")
    expect(patient.locator(".assessment-intro")).to_contain_text(period[1])
    doctor.goto(url + phq4)
    expect(doctor.locator("#item-1 .is-selected")).to_contain_text(
        "Более, чем половину этого времени"
    )
    expect(doctor.locator("#item-1")).to_contain_text("Синтетический ответ после паузы")
    expect(doctor.locator('input[name^="answer_"]')).to_have_count(0)

    # PHQ-4 automatically adds follow-ups in its original, yesterday-ended session.
    followups = {}
    for scale, name, count in (("phq9", "PHQ-9", 9), ("gad7", "GAD-7", 7)):
        patient.goto(url + "/patients/1/profile/assessments")
        patient.locator(".assessment-row").filter(has_text=name).first.get_by_role(
            "link"
        ).click()
        followups[scale] = patient.url.split("?")[0].removeprefix(url)
        expect(patient.locator("[data-assessment-period-note]")).to_be_visible()
        expect(patient.locator(".assessment-intro")).to_contain_text(period[1])
        for item in range(1, count + 1):
            choose(item, 1 if scale == "phq9" and item == 9 else 0)
            save("Завершить и отправить" if item == count else "Сохранить и продолжить")
        expect(patient.locator(".assessment-intro .badge")).to_have_text("Отправлено")
        doctor.goto(url + followups[scale])
        expect(doctor.locator(".assessment-intro .badge")).to_have_text("Отправлено")
        expect(doctor.locator(".assessment-intro")).to_contain_text(period[1])

    patient.goto(url + safety)
    for item in (1, 2):
        choose(item, 0)
        save()
    expect(patient.locator('[name="step_item"]')).to_have_value("6")
    choose(6, 1)
    save()
    expect(patient.locator('[name="step_item"]')).to_have_value("7")
    choose(7, 0)
    save("Завершить и отправить")
    expect(patient.locator(".assessment-intro .badge")).to_have_text("Отправлено")

    patient.goto(url + hcl)
    expect(patient.locator("[data-assessment-period-note]")).to_have_count(0)
    choose(1, 0)
    save("Сохранить и сделать паузу")

    # Native HTML save also works for a previous-day form with JavaScript off.
    nojs = patient.context.browser.new_context(java_script_enabled=False)
    nojs.add_cookies(patient.context.cookies())
    native = nojs.new_page()
    native.goto(url + followups["gad7"] + "?item=7")
    expect(native.locator("[data-assessment-period-note]")).to_be_visible()
    native.locator('[name="answer_7"][value="0"]').check()
    native.get_by_role("button", name="Завершить и отправить", exact=True).click()
    expect(native.locator(".assessment-intro .badge")).to_have_text("Отправлено")
    native.reload()
    expect(native.locator("#item-7 .is-selected")).to_contain_text("Совсем нет")
    nojs.close()

    audits = []
    for page, label, path in (
        (patient, "patient-resume", phq4 + "?item=1"),
        (doctor, "doctor-original-answers", phq4),
    ):
        for width in (320, 390, 768, 1280, 1920):
            page.set_viewport_size(dict(width=width, height=900))
            assert page.goto(url + path).status == 200
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            ), (label, width)
            if width in (390, 1280):
                page.screenshot(path=str(out / f"{label}-{width}.png"), full_page=True)
                page.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = page.evaluate(
                    """async()=> (await axe.run(document,{runOnly:{type:'tag',
                    values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations
                    .map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                )
                audits.append(dict(page=label, width=width, violations=violations))
                assert not violations, audits[-1]
    assert not errors, errors
    assert not failed_saves, failed_saves
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2)
    )
    print(
        "PASS assessment period: yesterday's PHQ-4 pause/reload/comment/discuss/back/"
        "finish, automatic PHQ-9/GAD-7 completed with original dates, old safety "
        "branch completed, HCL unchanged, no-JS completion, clinician read-only "
        "original answers, five widths, four axe checks, no failed saves or JS "
        "errors. Disposable synthetic DB only.",
        flush=True,
    )
