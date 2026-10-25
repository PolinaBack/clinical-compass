"""Questionnaire disclosure/audio and unified intake support on synthetic records."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_questionnaire_panels(patient, doctor, url, ids):
    out = Path("artifacts/questionnaire-panels")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    for page in (patient, doctor):
        page.on("pageerror", lambda error: errors.append(str(error)))
    for scale, aid in ids.items():
        patient.goto(f"{url}/assessments/{aid}")
        comment = patient.locator("[data-question-comment]")
        expect(comment.locator("#record-start")).to_have_count(1)
        assert not comment.evaluate("node=>node.open")
        assert patient.locator("form form").count() == 0
        assert (
            patient.locator("#recording-file").evaluate("node=>node.form.id")
            == "audio-form"
        )
        assert patient.locator("#assessment-form").evaluate(
            "node=>node.checkValidity()"
        )
    # Native answer submission must not require the optional recording fields.
    patient.goto(f'{url}/assessments/{ids["gad7"]}')
    patient.locator('[name="answer_1"][value="0"]').check()
    patient.get_by_role("button", name="Сохранить и сделать паузу", exact=True).click()
    expect(patient.locator('[name="answer_1"][value="0"]')).to_be_checked()
    assert "saved=1" in patient.url
    # Real MediaRecorder with the runner's artificial microphone; separate POST.
    aid = ids["phq9"]
    patient.goto(f"{url}/assessments/{aid}")
    patient.locator('[name="answer_1"][value="0"]').check()
    patient.get_by_role("button", name="Сохранить и сделать паузу", exact=True).click()
    patient.locator("[data-question-comment] > summary").click()
    patient.locator("#audio-consent").check()
    patient.locator("#record-start").click()
    expect(patient.locator("#record-stop")).to_be_enabled()
    patient.wait_for_timeout(1100)
    patient.locator("#record-stop").click()
    expect(patient.locator("#audio-preview")).to_be_visible()
    patient.locator('[name="transcript"]').fill(
        "Синтетическое голосовое пояснение к первому вопросу"
    )
    patient.locator('[name="transcript_reviewed"]').check()
    patient.get_by_role(
        "button", name="Сохранить голосовое пояснение", exact=True
    ).click()
    note = patient.locator(".questionnaire-voice-note")
    expect(note).to_contain_text("Синтетическое голосовое пояснение")
    patient.screenshot(path=str(out / "voice-1280.png"))
    audio = note.locator("audio").get_attribute("src")
    assert patient.request.get(url + audio).status == 200
    doctor.goto(f"{url}/assessments/{aid}")
    assert doctor.request.get(url + audio).status == 200
    note.get_by_role("button", name="Удалить запись", exact=True).click()
    expect(patient.locator(".questionnaire-voice-note")).to_have_count(0)
    # Finish a full scale and check the collapsed source answers and green status.
    aid = ids["phq4"]
    patient.goto(f"{url}/assessments/{aid}")
    for item in range(1, 5):
        patient.locator(f'[name="answer_{item}"][value="0"]').check()
        patient.get_by_role(
            "button",
            name="Завершить и отправить" if item == 4 else "Сохранить и продолжить",
            exact=True,
        ).click()
    originals = patient.locator("#original-answers")
    assert not originals.evaluate("node=>node.open")
    badge = patient.locator('[data-assessment-status="completed"]')
    expect(badge).to_have_text("Отправлено")
    assert badge.evaluate(
        "n=>parseFloat(getComputedStyle(n).fontSize)>=18&&n.getBoundingClientRect().height>=48"
    )
    assert (
        badge.evaluate("n=>getComputedStyle(n).backgroundColor") == "rgb(221, 243, 231)"
    )
    patient.screenshot(path=str(out / "sent-1280.png"))
    originals.locator(":scope > summary").click()
    expect(patient.locator("#item-1")).to_be_visible()
    doctor.goto(f"{url}/assessments/{aid}")
    assert not doctor.locator("#original-answers").evaluate("node=>node.open")
    doctor.goto(f"{url}/assessments/{aid}#item-2")
    expect(doctor.locator("#item-2")).to_be_visible()
    # Unified support opens the sole uploader and records a real local note.
    patient.goto(url + "/patients/1/profile/about/exams")
    expect(patient.locator(".intake-empty-panel,.intake-attachments")).to_have_count(0)
    assert patient.locator(".intake-support").evaluate(
        "n=>Boolean(n.compareDocumentPosition(document.querySelector('.intake-links-panel'))&Node.DOCUMENT_POSITION_FOLLOWING)"
    )
    patient.get_by_role("link", name="Голосовая заметка", exact=True).click()
    upload = patient.locator(".intake-support [data-local-recording]")
    upload.locator("[data-record-start]").click()
    expect(upload.locator("[data-record-stop]")).to_be_visible()
    patient.wait_for_timeout(1100)
    upload.locator("[data-record-stop]").click()
    expect(upload.locator("[data-record-preview]")).to_be_visible()
    upload.get_by_role(
        "button", name="Сохранить выбранные вложения", exact=True
    ).click()
    expect(
        patient.get_by_role("link", name="voice-note.webm", exact=True)
    ).to_be_visible()
    # Responsive/collapsed layouts and A/AA accessibility after the changes.
    audits = []
    routes = [
        f'/assessments/{ids["phq9"]}',
        f"/assessments/{aid}?review=1",
        "/patients/1/profile/about/personal",
        "/patients/1/profile/about/exams",
    ]
    for route in routes:
        for width in (320, 390, 768, 1280, 1920):
            patient.set_viewport_size(dict(width=width, height=1000))
            patient.goto(url + route)
            assert patient.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), (route, width)
            if width in (390, 1280):
                patient.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                violations = patient.evaluate(
                    "async()=>(await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"
                )
                audits.append(dict(route=route, width=width, violations=violations))
                assert not violations, audits[-1]
                if route.endswith("/personal"):
                    patient.screenshot(path=str(out / f"intake-{width}.png"))
                    patient.locator(".intake-support").evaluate(
                        "n=>window.scrollTo({top:scrollY+n.getBoundingClientRect().top-100,behavior:'instant'})"
                    )
                    patient.screenshot(path=str(out / f"support-{width}.png"))
    patient.set_viewport_size(dict(width=1280, height=1000))
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "PASS all five scales: optional voice inside comment disclosure, native answer save independent of audio, real microphone upload/play/delete; patient/doctor originals collapsed with anchor reveal, large green sent status; single intake uploader/real voice and related-card order; five widths, 8 axe audits, no JS errors.",
        flush=True,
    )
