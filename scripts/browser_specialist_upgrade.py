"""Acceptance on disposable synthetic accounts; models supplied by a test runner."""

import json
import os
from pathlib import Path
from playwright.sync_api import expect


def check_specialist_upgrade(doctor, patient, url, grant, process):
    out = Path("artifacts/specialist-upgrade")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    for page in (doctor, patient):
        page.on("pageerror", lambda e: errors.append(str(e)))
    doctor.goto(url + "/doctor")
    expect(doctor.locator(".patient-preview")).to_have_count(2)
    expect(doctor.locator("main")).not_to_contain_text("ИИ-анализ")
    expect(doctor.locator("#workflow-alerts")).to_have_count(0)
    assert [s.strip() for s in doctor.locator(".portal-nav a").all_text_contents()] == [
        "Пациенты",
        "Календарь",
        "Библиотека",
        "Форумы",
        "Настройки",
    ]
    doctor.locator(".patient-preview").first.get_by_role(
        "link", name="Карточка пациента", exact=True
    ).click()
    expect(
        doctor.get_by_role("heading", name="Краткое медицинское резюме", exact=True)
    ).to_be_visible()
    expect(doctor.locator(".medical-summary")).to_contain_text(
        "175 см · 75 кг · ИМТ 24.5"
    )
    expect(doctor.locator("main")).not_to_contain_text("PRIVATE_DRAFT_ONLY")
    expect(doctor.locator(".profile-nav")).to_have_count(0)
    doctor.get_by_role(
        "link", name="Анкеты, назначения и динамика", exact=True
    ).first.click()
    expect(doctor.locator("#assessments")).to_be_visible()
    expect(doctor.locator("#questionnaire-results")).to_be_visible()
    expect(doctor.locator(".specialist-nav")).not_to_contain_text("Безопасность")
    doctor.goto(url + "/doctor/forums")
    expect(
        doctor.get_by_role("link", name="Открыть форум врачей", exact=True)
    ).to_be_visible()
    expect(
        doctor.get_by_role("link", name="Открыть FAQ-форум", exact=True)
    ).to_be_visible()
    doctor.goto(url + "/doctor/calendar?week=2030-01-07")
    doctor.locator('[data-calendar-time="2030-01-07T10:00"]').click()
    form = doctor.locator("#calendar-event-form")
    expect(form.locator('[name="date"]')).to_have_value("2030-01-07")
    expect(form.locator('[name="time"]')).to_have_value("10:00")
    form.locator('[name="patient_id"]').select_option("")
    form.locator('[name="url"]').fill("https://example.org/synthetic-room")
    form.locator('[name="fallback"]').fill("Синтетический резервный контакт")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    expect(doctor.locator(".calendar-event.event-open")).to_contain_text("10:00–10:50")
    doctor.locator(".calendar-event.event-open").click()
    doctor.locator("[data-slot-book] select").select_option("1")
    doctor.get_by_role("button", name="Записать пациента на окно", exact=True).click()
    expect(doctor.locator(".calendar-event.event-planned")).to_contain_text(
        "10:00–10:50"
    )
    expect(doctor.locator(".calendar-visit")).to_contain_text("Демо-пациент А")
    patient.goto(url + "/patients/1/profile/video")
    expect(patient.locator("#video")).to_contain_text("2030-01-07")
    doctor.get_by_role("button", name="+ Запись прошедшей встречи", exact=True).click()
    form.locator('[name="date"]').fill("2020-01-01")
    form.locator('[name="patient_id"]').select_option("1")
    form.get_by_role("button", name="Сохранить время", exact=True).click()
    doctor.get_by_role("button", name="Подготовить запись встречи", exact=True).click()
    assert doctor.url.split("?")[0] == url + "/doctor/calendar"
    grant()
    doctor.reload()
    upload = doctor.locator('form[enctype="multipart/form-data"]')
    import io, wave

    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    upload.locator('[name="file"]').set_input_files(
        dict(
            name="synthetic-silence.wav", mimeType="audio/wav", buffer=stream.getvalue()
        )
    )
    upload.get_by_role("button", name="Загрузить запись", exact=True).click()
    doctor.get_by_role("button", name="Расшифровать запись", exact=True).click()
    process()
    doctor.reload()
    doctor.get_by_role(
        "button", name="Подготовить итоги и следующие шаги", exact=True
    ).click()
    process()
    doctor.reload()
    expect(doctor.locator(".meeting-summary")).to_contain_text("Обсуждался сон.")
    expect(doctor.locator(".meeting-summary")).to_contain_text("Следующие шаги")
    doctor.get_by_text("Обработка записи", exact=True).click()
    doctor.get_by_role("link", name="Показать итоги", exact=True).click()
    assert doctor.url.split("?")[0] == url + "/doctor/calendar"
    assert doctor.url.split("?")[0] == url + "/doctor/calendar"
    doctor.locator(".calendar-patient-picker select").select_option("2")
    expect(doctor.locator("main")).not_to_contain_text("synthetic-silence.wav")
    expect(doctor.locator("main")).not_to_contain_text("Обсуждался сон.")
    doctor.locator(".calendar-patient-picker select").select_option("1")
    expect(doctor.locator(".meeting-summary")).to_contain_text("Обсуждался сон.")
    calendar_url = url + "/doctor/calendar?week=2030-01-07&patient_id=1"
    assert patient.request.get(url + "/doctor/calendar").status == 403
    assert patient.request.get(url + "/doctor/forums").status == 403
    for name, route in [
        ("calendar", calendar_url),
        ("profile", url + "/patients/1/profile"),
        ("assessments", url + "/patients/1/workspace/assessments"),
        ("forums", url + "/doctor/forums"),
        ("patients", url + "/doctor"),
    ]:
        for width in (320, 390, 768, 1280):
            doctor.set_viewport_size(dict(width=width, height=900))
            doctor.goto(route)
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth+1"
            ), (name, width)
            doctor.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
            violations = doctor.evaluate(
                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
            )
            audits.append(dict(page=name, width=width, violations=violations))
            assert not violations, audits[-1]
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS specialist upgrade: real calendar click -> availability -> booking -> historical meeting -> inline upload/queue/transcript/summary with EXPLICIT SYNTHETIC MODEL FIXTURE; patient selection isolates sources; submitted-only profile/BMI, merged questionnaires/graphs, both forums, patient ACL; 320/390/768/1280; 20 axe audits, no overflow or JS errors. Real ASR/Ollama not tested.",
        flush=True,
    )
