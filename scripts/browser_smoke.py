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
        patient, doctor = pc.new_page(), dc.new_page()
        errors = []
        for page in [patient, doctor]:
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.emulate_media(reduced_motion="reduce")
        Path("artifacts").mkdir(exist_ok=True)

        def capture(page, name):
            page.evaluate("window.scrollTo(0,0)")
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), name
            page.screenshot(path=f"artifacts/{name}.png", full_page=True)

        def answer_questions(values, comment_first=False):
            for i, value in enumerate(values, 1):
                expect(patient.locator(".question-card")).to_have_count(1)
                patient.locator(f'input[name="answer_{i}"][value="{value}"]').check()
                if i == 1 and comment_first:
                    patient.get_by_text(
                        "Добавить комментарий или обсудить", exact=True
                    ).click()
                    patient.get_by_label("Комментарий к вопросу 1", exact=True).fill(
                        "Синтетическая тема для обсуждения"
                    )
                    patient.get_by_label("! Хочу обсудить").check()
                patient.get_by_role(
                    "button",
                    name=(
                        "Сохранить и продолжить"
                        if i < len(values)
                        else "Завершить и отправить"
                    ),
                    exact=True,
                ).click()

        patient.goto(url)
        capture(patient, "home-desktop")
        patient.get_by_role("button", name="Следующие полезные материалы").click()
        patient.wait_for_function(
            'document.getElementById("resource-track").scrollLeft>0'
        )
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "home-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})
        patient.get_by_role("link", name="Я пациент", exact=True).click()
        patient.get_by_label("Логин", exact=True).fill(patient_user)
        patient.get_by_label("Пароль", exact=True).fill(patient_password)
        patient.get_by_role("button", name="Войти как пациент").click()
        expect(patient.locator(".role-badge")).to_contain_text("Пациент")
        patient.get_by_role("link", name="Начать знакомство").click()
        expect(patient.get_by_role("heading", name="Прежде чем начать")).to_be_visible()
        expect(patient.locator(".trust-questions details")).to_have_count(3)
        expect(
            patient.locator(".trust-questions").get_by_role(
                "link", name="Все вопросы и ответы"
            )
        ).to_be_visible()
        capture(patient, "introduction-desktop")
        patient.get_by_role("link", name="Посмотреть условия участия").click()
        patient.get_by_label("Мне 18 лет").check()
        patient.get_by_label("Я ознакомился").check()
        patient.get_by_role("button", name="Продолжить", exact=True).click()
        expect(
            patient.get_by_role("heading", name="C-SSRS · беседа о безопасности")
        ).to_be_visible()
        patient.get_by_role(
            "button", name="Начать / продолжить вопросы о безопасности"
        ).click()
        for item in [1, 2, 6]:
            expect(patient.locator(".question-card")).to_have_count(1)
            patient.locator(f'input[name="answer_{item}"][value="0"]').check()
            if item == 1:
                capture(patient, "safety-desktop")
                patient.set_viewport_size({"width": 390, "height": 844})
                capture(patient, "safety-mobile")
                patient.set_viewport_size({"width": 1280, "height": 900})
            patient.get_by_role(
                "button",
                name="Сохранить и продолжить" if item != 6 else "Завершить и отправить",
                exact=True,
            ).click()
        expect(
            patient.get_by_text("Это не заключение об отсутствии риска.", exact=False)
        ).to_be_visible()
        patient.get_by_role("button", name="Продолжить с PHQ-4").click()
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "question-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})
        answer_questions([1, 2, 1, 2], comment_first=True)
        expect(patient.get_by_role("status").first).to_contain_text("Сохранено")

        doctor.goto(url + "/login/specialist")
        doctor.get_by_label("Логин", exact=True).fill(specialist_user)
        doctor.get_by_label("Пароль", exact=True).fill(specialist_password)
        doctor.get_by_role("button", name="Войти как специалист").click()
        doctor.goto(url + "/patients/1")
        expect(doctor.get_by_text("Предложение по PHQ-2", exact=False)).to_be_visible()
        doctor.goto(url + "/clinical-tools?patient_id=1")
        doctor.get_by_role("button", name="Назначить PHQ-ADS", exact=True).click()
        patient.goto(url + "/patients/1/profile")
        patient.locator(".assessment-row").filter(has_text="PHQ-9").get_by_role(
            "link"
        ).click()
        for i in range(1, 10):
            patient.locator(
                f'input[name="answer_{i}"][value="{1 if i == 9 else 0}"]'
            ).check()
            patient.get_by_role(
                "button",
                name="Сохранить и продолжить" if i < 9 else "Сохранить и сделать паузу",
                exact=True,
            ).click()
        # Synthetic microphone. Save the standard answer before uploading audio.
        patient.get_by_text("Голосовой комментарий к вопросу 9", exact=True).click()
        patient.locator("#audio-consent").check()
        patient.locator("#record-start").click()
        expect(patient.locator("#record-stop")).to_be_enabled()
        patient.wait_for_timeout(1100)
        patient.locator("#record-stop").click()
        expect(patient.locator("#recording-file")).not_to_have_value("")
        patient.get_by_role("button", name="Сохранить голосовое пояснение").click()
        expect(patient.locator('audio[src^="/audio/"]')).to_have_count(1)
        doctor.goto(url + "/doctor")
        expect(doctor.locator("#workflow-alerts")).to_contain_text("нужно уточнение")
        doctor.locator("#workflow-alerts a").first.click()
        doctor.get_by_role("button", name="Подтвердить получение события").click()
        doctor.get_by_label("Документировать действие").fill(
            "Синтетическое документированное действие"
        )
        doctor.get_by_role("button", name="Сохранить действие специалиста").click()
        expect(doctor.locator("#conversation")).to_contain_text(
            "Спасибо, что рассказали"
        )
        patient.get_by_role("button", name="Завершить и отправить", exact=True).click()
        patient.goto(url + "/patients/1/profile")
        patient.locator(".assessment-row").filter(has_text="GAD-7").get_by_role(
            "link"
        ).click()
        answer_questions([0] * 7)
        patient.goto(url + "/patients/1/results")
        expect(patient.locator(".symptom-chart")).to_have_count(2)

        patient.goto(url + "/me")
        patient.get_by_label("Заметка", exact=True).fill(
            "Синтетическая браузерная запись"
        )
        patient.get_by_role("button", name="Сохранить запись").click()
        doctor.goto(url + "/patients/1")
        doctor.get_by_text("Оставить комментарий к записи", exact=True).click()
        doctor.get_by_label("Комментарий для пациента").fill(
            "Учебная обратная связь специалиста"
        )
        doctor.get_by_role("button", name="Добавить комментарий").click()
        patient.reload()
        expect(
            patient.get_by_text("Учебная обратная связь специалиста", exact=True)
        ).to_be_visible()
        capture(patient, "patient-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "patient-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})

        doctor.goto(url + "/patients/1/profile")
        doctor.get_by_text("Подготовить отчёт", exact=True).click()
        doctor.get_by_label("Название", exact=True).fill(
            "Синтетический отчёт после встречи"
        )
        doctor.get_by_label("Заключение специалиста", exact=True).fill(
            "Учебное заключение специалиста"
        )
        doctor.get_by_label("Дальнейший план", exact=True).fill(
            "Учебный план наблюдения"
        )
        doctor.get_by_label("Поделиться с пациентом после сохранения").check()
        doctor.get_by_role("button", name="Сохранить отчёт", exact=True).click()
        patient.goto(url + "/patients/1/profile")
        expect(
            patient.get_by_text("Учебное заключение специалиста", exact=True)
        ).to_be_visible()
        patient.get_by_label("Обо мне", exact=True).fill(
            "Учебная биография для браузерного сценария"
        )
        patient.get_by_role("button", name="Сохранить профиль").click()
        patient.get_by_label("Вымышленное имя").fill("Демо-мать")
        patient.get_by_label("Отношение к пациенту").select_option("mother")
        patient.get_by_role("button", name="Добавить участника").click()
        parent = patient.locator("select[name=parent_id] option").last.get_attribute(
            "value"
        )
        patient.get_by_label("Вымышленное имя").fill("Демо-ребёнок")
        patient.get_by_label("Отношение к пациенту").select_option("child")
        patient.get_by_label("Родитель этого участника").select_option(parent)
        patient.get_by_role("button", name="Добавить участника").click()
        expect(patient.locator("#genogram svg line")).to_have_count(1)
        capture(patient, "profile-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "profile-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})

        patient.goto(url + "/patients/1/contact")
        patient.locator('form[action="/patients/1/questions"] textarea').fill(
            "Учебный вопрос перед обследованием"
        )
        patient.get_by_role("button", name="Отправить личный вопрос").click()
        expect(patient.locator("#questions")).to_contain_text(
            "Передан в локальную очередь"
        )
        doctor.goto(url + "/patients/1/contact")
        doctor.get_by_role("button", name="Подтвердить прочтение вопроса").click()
        doctor.get_by_label("Ответ пациенту", exact=True).fill(
            "Учебный ответ на личный вопрос"
        )
        doctor.get_by_role("button", name="Отправить ответ", exact=True).click()
        doctor.get_by_text("Назначить видеоприём", exact=True).click()
        doctor.get_by_label("Дата и время", exact=True).fill("2026-12-01T10:30")
        doctor.get_by_label("HTTPS-ссылка согласованного сервиса").fill(
            "https://example.org/synthetic-video"
        )
        doctor.get_by_label("Как связаться при обрыве").fill(
            "Учебный резервный канал в согласованное время"
        )
        doctor.get_by_role("button", name="Сохранить видеоприём").click()
        patient.reload()
        expect(
            patient.get_by_text("Учебный ответ на личный вопрос", exact=True)
        ).to_be_visible()
        expect(
            patient.get_by_role("link", name="Открыть видеоприём")
        ).to_have_attribute("href", "https://example.org/synthetic-video")
        patient.get_by_label("Вопрос врачу", exact=True).fill(
            "Синтетическое сообщение специалисту"
        )
        patient.get_by_role("button", name="Отправить", exact=True).click()
        capture(patient, "contact-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "contact-mobile")
        doctor.goto(url + "/patients/1")
        capture(doctor, "specialist-desktop")
        doctor.set_viewport_size({"width": 390, "height": 844})
        capture(doctor, "specialist-mobile")
        assert not errors, errors
        browser.close()
    print(
        "UI check passed: separate portals, trust/FAQ, teaching safety adaptation with hidden branches, stepped PHQ-4/9/GAD-7, PHQ-ADS assignment, safety events, synthetic audio, diary comments, profile/genogram, published report, personal question/reply, video link, desktop/mobile; no JS errors."
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
