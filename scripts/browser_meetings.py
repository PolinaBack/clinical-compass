"""Synthetic acceptance: callback processes only the disposable test database."""

import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from playwright.sync_api import expect


def check_meetings(patient, doctor, url, run_job):
    """The disposable worker callback reports progress across a five-second poll."""
    errors = []
    for page in (doctor, patient):
        page.on("pageerror", lambda error: errors.append(str(error)))
    doctor.goto(url + "/patients/1/profile/video")
    doctor.get_by_text("Назначить видеоприём", exact=True).click()
    form = doctor.locator('form[action="/patients/1/appointments"]')
    form.locator('[name="starts_at"]').fill("2026-10-21T15:00")
    form.locator('[name="url"]').fill("https://example.org/synthetic-meeting")
    form.locator('[name="fallback"]').fill("Синтетический резервный способ связи")
    form.get_by_role("button", name="Сохранить видеоприём", exact=True).click()
    doctor.get_by_role(
        "link", name="Записи встреч, расшифровки и проверенные итоги", exact=True
    ).click()
    doctor.get_by_role(
        "button", name="Подготовить расшифровку встречи", exact=True
    ).first.click()
    meeting_path = doctor.url.removeprefix(url)
    mid = meeting_path.split("/")[-1]

    def give(page):
        page.goto(url + meeting_path)
        for purpose in ("video_recording", "ai_transcription", "ai_summary"):
            details = (
                page.locator("details")
                .filter(
                    has=page.locator("summary").filter(
                        has_text=re.compile("^Согласия именно этой встречи$")
                    )
                )
                .first
            )
            if details.get_attribute("open") is None:
                details.locator("summary").first.click()
            form = page.locator(f'form[action="/meetings/{mid}/consents/{purpose}"]')
            form.locator('input[name="confirmed"]').check()
            form.locator('button[value="granted"]').click()

    give(doctor)
    give(patient)
    doctor.goto(url + meeting_path)
    with TemporaryDirectory(prefix="compass-browser-video-") as folder:
        path = Path(folder) / "synthetic.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=160x120:d=1",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=25,
        )
        content = path.read_bytes()
    doctor.get_by_label("Вид записи", exact=True).select_option("video")
    doctor.get_by_label("Файл записи", exact=True).set_input_files(
        {
            "name": "synthetic-silence.mp4",
            "mimeType": "video/mp4",
            "buffer": content,
        }
    )
    doctor.get_by_role(
        "button", name="Загрузить с проверкой согласий", exact=True
    ).click()
    doctor.locator("video").evaluate(
        "async el => {el.muted=true; await el.play(); el.pause();}"
    )
    media_path = doctor.locator("video").get_attribute("src")
    doctor.get_by_role("button", name="Расшифровать запись", exact=True).click()
    # Observe real polling while the external callback handles only the disposable DB.
    doctor.evaluate(
        "window.progressMessages=[]; new MutationObserver(()=>window.progressMessages.push(document.querySelector('[data-job-progress]')?.textContent)).observe(document.querySelector('.meeting-recording'),{subtree:true,childList:true,characterData:true})"
    )
    run_job()
    expect(doctor.locator("[data-meeting-job]").first).to_have_attribute(
        "data-job-state", "done", timeout=15000
    )
    expect(doctor.locator("[data-job-ready]").first).to_be_visible()
    assert doctor.evaluate(
        "window.progressMessages.some(message=>message?.includes('Распознано до'))"
    )
    doctor.locator("[data-job-ready] a").first.click()
    doctor.get_by_text("Расшифровка · версия", exact=False).click()
    doctor.get_by_label("Говорящий · фрагмент 1", exact=True).fill(
        "Пациент — синтетический пример"
    )
    doctor.get_by_label("Говорящий · фрагмент 2", exact=True).fill(
        "Врач — синтетический пример"
    )
    doctor.get_by_role(
        "button", name="Сохранить правки расшифровки", exact=True
    ).click()
    doctor.get_by_role(
        "button", name="Подготовить итоги и следующие шаги", exact=True
    ).click()
    doctor.get_by_text("Расшифровка · версия", exact=False).click()
    doctor.get_by_label("Текст · фрагмент 1", exact=False).fill(
        "НЕСОХРАНЁННАЯ тестовая правка"
    )
    run_job()
    expect(doctor.locator("[data-meeting-job]").first).to_have_attribute(
        "data-job-state", "done", timeout=15000
    )
    expect(doctor.get_by_label("Текст · фрагмент 1", exact=False)).to_have_value(
        "НЕСОХРАНЁННАЯ тестовая правка"
    )
    doctor.reload()
    patient.goto(url + meeting_path)
    expect(patient.get_by_text("Обсуждался сон.", exact=True)).to_have_count(0)
    expect(
        patient.locator('input[type="file"],video,audio,[data-meeting-job]')
    ).to_have_count(0)
    assert patient.request.get(url + media_path).status == 403
    doctor.get_by_label(
        "Я проверил(а) смысл каждого оставленного пункта", exact=False
    ).check()
    doctor.get_by_label(
        "Я проверил(а) по записи все названия лекарств", exact=False
    ).check()
    doctor.get_by_role(
        "button", name="Подтвердить итоги и показать пациенту", exact=True
    ).click()
    patient.reload()
    expect(patient.get_by_text("Обсуждался сон.", exact=True)).to_be_visible()
    expect(patient.get_by_text("Уточнить детали сна.", exact=True)).to_have_count(0)
    expect(
        patient.locator('input[type="file"],video,audio,[data-meeting-seek]')
    ).to_have_count(0)
    expect(patient.locator(".meeting-transcript")).to_have_count(0)
    assert (
        patient.request.get(url + media_path, headers={"Range": "bytes=0-43"}).status
        == 403
    )
    doctor.locator("video").evaluate(
        "async el => {el.muted=true; await el.play(); el.pause();}"
    )
    doctor.get_by_role("button", name="00:00 · фрагмент 2", exact=True).click()
    expect(doctor.locator("video")).to_have_js_property("currentTime", 0.45)
    audits = []
    axe = os.getenv("COMPASS_AXE_PATH")
    Path("artifacts").mkdir(exist_ok=True)
    for page, role in ((doctor, "doctor"), (patient, "patient")):
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.goto(url + meeting_path)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                role,
                width,
            )
            if width in (390, 1280):
                if axe:
                    page.add_script_tag(path=axe)
                    violations = page.evaluate(
                        """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                    )
                    audits.append(dict(role=role, width=width, violations=violations))
                    Path("artifacts/meetings-accessibility.json").write_text(
                        json.dumps(audits, ensure_ascii=False, indent=2)
                    )
                page.screenshot(
                    path=f"artifacts/meeting-{role}-{width}.png", full_page=True
                )
    doctor.get_by_role("button", name="Снять публикацию итогов", exact=True).click()
    patient.goto(url + meeting_path)
    expect(patient.get_by_text("Обсуждался сон.", exact=True)).to_have_count(0)
    assert not errors, errors
    if axe:
        assert not any(a["violations"] for a in audits), audits
    print(
        "PASS meetings UI: scoped consent, real MP4/doctor playback/seek, live progress polling without losing unsaved text, synthetic worker, corrections, cited draft, clinician review/withdrawal, patient denied progress/upload/player/direct media/full transcript, four widths."
    )
