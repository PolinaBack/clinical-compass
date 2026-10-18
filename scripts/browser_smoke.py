"""Optional UI check. Use a disposable DB and synthetic local accounts only."""

import argparse
import getpass
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def run(
    url,
    patient_password,
    specialist_password,
    executable=None,
    patient_user="patient-a",
    specialist_user="specialist",
):
    with sync_playwright() as p:
        options = {
            "headless": True,
            "args": [
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        }
        if executable:
            options["executable_path"] = executable
            options["args"].append("--no-sandbox")
        browser = p.chromium.launch(**options)
        pc = browser.new_context(
            viewport={"width": 1280, "height": 900}, permissions=["microphone"]
        )
        dc = browser.new_context(viewport={"width": 1280, "height": 900})
        patient = pc.new_page()
        patient.emulate_media(reduced_motion="reduce")
        doctor = dc.new_page()
        doctor.emulate_media(reduced_motion="reduce")
        errors = []
        for page in [patient, doctor]:
            page.on("pageerror", lambda e: errors.append(str(e)))
        Path("artifacts").mkdir(exist_ok=True)
        patient.goto(url)
        patient.screenshot(path="artifacts/home-desktop.png", full_page=True)
        track = patient.locator("#resource-track")
        patient.get_by_role("button", name="Следующие полезные материалы").click()
        patient.wait_for_function(
            'document.getElementById("resource-track").scrollLeft>0'
        )
        patient.set_viewport_size({"width": 390, "height": 844})
        assert patient.evaluate("document.documentElement.scrollWidth<=innerWidth")
        patient.evaluate(
            "window.scrollTo(0,0);document.getElementById('resource-track').scrollLeft=0"
        )
        patient.screenshot(path="artifacts/home-mobile.png", full_page=True)
        patient.set_viewport_size({"width": 1280, "height": 900})
        patient.get_by_role("link", name="Я пациент →", exact=True).click()
        patient.get_by_label("Логин", exact=True).fill(patient_user)
        patient.get_by_label("Пароль", exact=True).fill(patient_password)
        patient.get_by_role("button", name="Войти как пациент").click()
        patient.get_by_role("link", name="Посмотреть условия").click()
        patient.get_by_label("Мне 18 лет").check()
        patient.get_by_label("Я ознакомился").check()
        patient.get_by_role("button", name="Продолжить →", exact=True).click()
        expect(patient.locator(".role-badge").first).to_contain_text("Пациент")
        assert patient.get_by_role("link", name="Инструменты", exact=True).count() == 0
        patient.get_by_role("button", name="Начать / продолжить PHQ-4").click()
        for i, value in enumerate([1, 2, 1, 2], 1):
            patient.locator(f'input[name="answer_{i}"][value="{value}"]').check()
        question = patient.locator("#item-1")
        question.locator("summary").click()
        question.locator("textarea").fill("Синтетическая тема для обсуждения")
        question.get_by_label("! Хочу обсудить").check()
        patient.get_by_role("button", name="Завершить и отправить").click()
        expect(patient.get_by_role("status").first).to_contain_text("Сохранено")
        doctor.goto(url + "/login/specialist")
        doctor.get_by_label("Логин", exact=True).fill(specialist_user)
        doctor.get_by_label("Пароль", exact=True).fill(specialist_password)
        doctor.get_by_role("button", name="Войти как специалист").click()
        expect(doctor.locator(".role-badge")).to_contain_text("Специалист")
        doctor.get_by_role("link", name="Карточка пациента").first.click()
        expect(doctor.get_by_text("Предложение по PHQ-2", exact=False)).to_be_visible()
        doctor.locator("details.inset summary").click()
        doctor.locator('select[name="scale"]').select_option("phq9")
        doctor.get_by_role("button", name="Подтвердить назначение").click()
        doctor.locator("details.inset summary").click()
        doctor.locator('select[name="scale"]').select_option("gad7")
        doctor.get_by_role("button", name="Подтвердить назначение").click()
        patient.goto(url + "/me")
        patient.locator(".assessment-row").filter(has_text="PHQ-9").get_by_role(
            "link"
        ).click()
        for i in range(1, 10):
            patient.locator(
                f'input[name="answer_{i}"][value="{1 if i==9 else 0}"]'
            ).check()
        patient.get_by_role("button", name="Сохранить и сделать паузу").click()
        doctor.goto(url + "/doctor")
        expect(doctor.locator("#workflow-alerts")).to_contain_text("нужно уточнение")
        doctor.locator("#workflow-alerts a").first.click()
        doctor.get_by_role("button", name="Подтвердить получение события").click()
        doctor.get_by_label("Документировать действие").fill(
            "Синтетический тест: сведения просмотрены, действие демонстрируется."
        )
        doctor.get_by_role("button", name="Сохранить действие специалиста").click()
        patient.get_by_role("button", name="Завершить и отправить").click()
        # Mock microphone: raw recording stays local, no cloud recognizer.
        patient.locator("#audio-consent").check()
        patient.locator("#record-start").click()
        expect(patient.locator("#record-stop")).to_be_enabled()
        patient.wait_for_timeout(1200)
        patient.locator("#record-stop").click()
        expect(patient.locator("#recording-file")).not_to_have_value("")
        patient.get_by_role("button", name="Сохранить голосовое пояснение").click()
        expect(
            patient.get_by_text("Без расшифровки: исходная запись доступна.")
        ).to_be_visible()
        patient.goto(url + "/me")
        patient.locator(".assessment-row").filter(has_text="GAD-7").get_by_role(
            "link"
        ).click()
        for i in range(1, 8):
            patient.locator(f'input[name="answer_{i}"][value="0"]').check()
        patient.get_by_role("button", name="Завершить и отправить").click()
        patient.goto(url + "/patients/1/results")
        expect(patient.locator(".symptom-chart")).to_have_count(2)
        patient.goto(url + "/me")
        patient.get_by_label("Заметка", exact=True).fill(
            "Синтетическая браузерная запись"
        )
        patient.get_by_role("button", name="Сохранить запись").click()
        patient.get_by_label("Вопрос врачу", exact=True).fill(
            "Синтетический вопрос из отдельного кабинета"
        )
        patient.get_by_role("button", name="Отправить", exact=True).click()
        patient.locator(".section-fold").filter(
            has_text="Семейная схема · генограмма"
        ).locator("summary").click()
        patient.get_by_label("Вымышленное имя").fill("Демо-мать")
        patient.get_by_label("Отношение к пациенту").select_option("mother")
        patient.get_by_role("button", name="Добавить участника").click()
        patient.locator(".section-fold").filter(
            has_text="Семейная схема · генограмма"
        ).locator("summary").click()
        parent = patient.locator("select[name=parent_id] option").last.get_attribute(
            "value"
        )
        patient.get_by_label("Вымышленное имя").fill("Демо-ребёнок")
        patient.get_by_label("Отношение к пациенту").select_option("child")
        patient.get_by_label("Родитель этого участника").select_option(parent)
        patient.get_by_role("button", name="Добавить участника").click()
        assert patient.locator("#genogram svg line").count() == 1
        patient.evaluate("window.scrollTo(0,0)")
        patient.screenshot(path="artifacts/patient-desktop.png", full_page=True)
        patient.set_viewport_size({"width": 390, "height": 844})
        assert patient.evaluate("document.documentElement.scrollWidth<=innerWidth")
        patient.evaluate("window.scrollTo(0,0)")
        patient.screenshot(path="artifacts/patient-mobile.png", full_page=True)
        doctor.goto(url + "/doctor")
        doctor.screenshot(path="artifacts/specialist-desktop.png", full_page=True)
        assert not errors, errors
        browser.close()
    print(
        "UI check passed: separate authenticated portals, carousel, consent, PHQ-4/9 and GAD-7, assignments, safety event lifecycle, local audio with synthetic microphone, diary, messages, genogram, desktop/mobile. No JS errors."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--executable", default=None)
    args = parser.parse_args()
    patient_password = os.getenv("COMPASS_PATIENT_PASSWORD") or getpass.getpass(
        "Пароль patient-a: "
    )
    specialist_password = os.getenv("COMPASS_SPECIALIST_PASSWORD") or getpass.getpass(
        "Пароль specialist: "
    )
    run(args.url, patient_password, specialist_password, args.executable)
