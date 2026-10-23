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
            viewport={"width": 1280, "height": 900},
            permissions=["microphone", "camera"],
        )
        dc = browser.new_context(viewport={"width": 1280, "height": 900})
        patient, doctor = pc.new_page(), dc.new_page()
        # Allow for slow cold starts in shared CI/cloud environments.
        patient.set_default_timeout(60000)
        doctor.set_default_timeout(60000)
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
        expect(patient.locator(".portal-nav")).to_have_count(1)
        assert patient.url.endswith("/patients/1/diary")
        patient.goto(url + "/me/start")
        expect(
            patient.get_by_role("heading", name="Здесь можно отвечать честно")
        ).to_be_visible()
        labels = patient.locator(".portal-nav a").all_text_contents()
        assert [label.strip() for label in labels] == [
            "Дневник",
            "Статистика",
            "Материалы",
            "Профиль",
            "С чего начать",
            "Настройки",
        ]
        expect(patient.locator(".trust-questions details")).to_have_count(3)
        expect(
            patient.locator(".trust-questions").get_by_role(
                "link", name="Все вопросы и ответы"
            )
        ).to_be_visible()
        capture(patient, "introduction-desktop")
        patient.get_by_role("link", name="Перейти к анкетам", exact=True).click()
        expect(patient.locator(".initial-assessment")).to_have_count(2)
        capture(patient, "questionnaires-desktop")
        patient.get_by_role("link", name="Посмотреть условия участия").first.click()
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
        doctor.goto(url + "/patients/1/workspace/assessments")
        expect(doctor.get_by_text("Предложение по PHQ-2", exact=False)).to_be_visible()
        doctor.goto(url + "/clinical-tools?patient_id=1")
        doctor.get_by_role("button", name="Назначить PHQ-ADS", exact=True).click()
        patient.goto(url + "/patients/1/profile/assessments")
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
        doctor.get_by_role("link", name="Подготовка к беседе", exact=True).click()
        expect(doctor.locator("#conversation")).to_contain_text(
            "Спасибо, что рассказали"
        )
        patient.get_by_role("button", name="Завершить и отправить", exact=True).click()
        patient.goto(url + "/patients/1/profile/assessments")
        patient.locator(".assessment-row").filter(has_text="GAD-7").get_by_role(
            "link"
        ).click()
        answer_questions([0] * 7)
        patient.goto(url + "/patients/1/results")
        expect(patient.locator(".symptom-chart")).to_have_count(3)

        patient.goto(url + "/patients/1/diary")
        patient.locator('[data-open="wellbeing-dialog"]').click()
        patient.get_by_label("Самочувствие, 0–10", exact=True).fill("6")
        patient.get_by_role("button", name="Применить оценку", exact=True).click()
        patient.locator('[data-open="sleep-dialog"]').click()
        patient.get_by_label("Продолжительность сна, часы", exact=True).fill("7.5")
        patient.get_by_role("button", name="Применить сон", exact=True).click()
        patient.get_by_label("Заметка", exact=True).fill(
            "Синтетическая браузерная запись"
        )
        patient.get_by_role("button", name="Сохранить запись").click()
        expect(patient.locator(".diary-chart")).to_have_count(1)
        expect(patient.locator(".axis-label")).to_have_count(2)
        doctor.goto(url + "/patients/1/diary")
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

        patient.get_by_label("Новая заметка", exact=True).fill(
            "Учебная заметка к следующей консультации"
        )
        patient.get_by_role("button", name="Сохранить заметку").click()
        doctor.goto(url + "/patients/1/diary")
        expect(
            doctor.get_by_text("Учебная заметка к следующей консультации", exact=True)
        ).to_be_visible()
        assert doctor.goto(url + "/patients/1/reports").status == 410
        assert patient.goto(url + "/patients/1/reports").status == 410
        patient.goto(url + "/patients/1/results")
        expect(patient.locator("#reports")).to_have_count(0)
        expect(patient.locator(".axis-label")).to_have_count(6)
        expect(patient.locator(".diary-chart")).to_have_count(0)
        capture(patient, "dynamics-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "dynamics-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})
        patient.goto(url + "/patients/1/profile")
        expect(patient.locator(".profile-tile")).to_have_count(0)
        assert patient.url.endswith("/profile/about")
        expect(
            patient.get_by_role(
                "heading", name="Первичная анкета «Обо мне»", exact=True
            )
        ).to_be_visible()
        capture(patient, "profile-desktop")
        patient.goto(url + "/patients/1/profile/about")
        patient.get_by_text("Прежние свободные заметки «Обо мне»", exact=True).click()
        patient.get_by_label("Обо мне", exact=True).fill("Учебная биография")
        patient.get_by_role("button", name="Сохранить профиль").click()
        patient.goto(url + "/patients/1/profile/family")
        expect(patient.locator("a.family-node-leaf")).to_have_count(6)
        patient.goto(url + "/patients/1/profile/family/member/mother")
        patient.get_by_label("Имя или родство", exact=True).fill("Учебная мама")
        patient.get_by_label("Что хотите рассказать о близком").fill(
            "Вымышленный контекст"
        )
        patient.get_by_role("button", name="Сохранить сведения", exact=True).click()
        patient.get_by_role("link", name="Что известно", exact=True).click()
        patient.get_by_label(
            "Какие заболевания или особенности здоровья Вам известны?"
        ).fill("Учебные сведения со слов близкого")
        patient.get_by_label("Есть заболевания, могу вспомнить", exact=True).check()
        patient.get_by_role("button", name="Сохранить сведения", exact=True).click()
        add_form = patient.get_by_role("form", name="Добавить сведения о близком")
        add_form.get_by_label("Напишите то, что помните").fill(
            "Сообщённое учебное название"
        )
        add_form.get_by_text(
            "Добавить описание, возраст и источник", exact=True
        ).click()
        add_form.get_by_label("Что вспомнили").fill("Учебный рассказ близкого")
        add_form.get_by_label("Примерно сколько лет?").fill("Около 48 лет")
        add_form.get_by_label("Откуда Вам это известно?").select_option(
            label="Со слов родственников"
        )
        add_form.get_by_label("Могу ошибаться, нужно уточнить").check()
        add_form.get_by_role("button", name="Добавить сведения", exact=True).click()
        expect(
            patient.get_by_role("heading", name="Сообщённое учебное название")
        ).to_be_visible()
        saved_form = patient.get_by_role(
            "form", name="Уточнить: Сообщённое учебное название"
        )
        saved_form.get_by_label("Примерно сколько лет?").fill("Около 49 лет")
        saved_form.get_by_role("button", name="Сохранить карточку").click()
        expect(
            patient.get_by_role(
                "form", name="Уточнить: Сообщённое учебное название"
            ).get_by_label("Примерно сколько лет?")
        ).to_have_value("Около 49 лет")
        capture(patient, "family-member-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "family-member-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})
        patient.get_by_role("link", name="Пояснения", exact=True).click()
        patient.get_by_label("Что важно уточнить").fill("Учебная тема к консультации")
        patient.get_by_label("Хочу обсудить на консультации").check()
        patient.get_by_role("button", name="Сохранить сведения", exact=True).click()
        patient.get_by_role("link", name="К дереву", exact=True).click()
        expect(
            patient.locator('.family-node-leaf[href$="/mother"] .family-discuss')
        ).to_have_attribute("aria-label", "Тема для обсуждения")
        patient.locator("#family-add-leaf").click()
        patient.locator('#family-add-dialog [name="name"]').fill("Учебная сестра")
        patient.locator('#family-add-dialog [name="relation"]').select_option("sister")
        patient.locator("#family-add-dialog details summary").click()
        patient.locator('#family-add-dialog [name="parent_id"]').select_option(
            label="Учебная мама"
        )
        patient.get_by_role("button", name="Добавить участника", exact=True).click()
        expect(patient.locator("a.family-node-leaf")).to_have_count(7)
        expect(
            patient.locator(".family-node-leaf").filter(has_text="Учебная сестра")
        ).to_be_visible()
        capture(patient, "family-desktop")
        patient.set_viewport_size({"width": 390, "height": 844})
        capture(patient, "family-mobile")
        patient.set_viewport_size({"width": 1280, "height": 900})
        from datetime import datetime, timedelta

        doctor.goto(url + "/doctor/calendar")
        form = doctor.locator('form[action="/doctor/calendar/slots"]')
        form.locator('[name="starts_at"]').fill(
            (datetime.now() + timedelta(days=15))
            .replace(hour=10, minute=30)
            .isoformat(timespec="minutes")
        )
        form.locator('[name="url"]').fill("https://example.org/synthetic-video")
        form.locator('[name="fallback"]').fill("Учебный резервный канал")
        form.get_by_role(
            "button", name="Опубликовать свободное окно", exact=True
        ).click()
        patient.goto(url + "/patients/1/profile/video")
        patient.get_by_label("Свободное время врача").select_option(index=1)
        patient.get_by_role(
            "button", name="Записаться на консультацию", exact=True
        ).click()
        expect(patient.get_by_text("Время подтверждено", exact=True)).to_be_visible()
        patient.reload()
        expect(
            patient.get_by_role("link", name="Открыть видеоприём")
        ).to_have_attribute("href", "https://example.org/synthetic-video")
        capture(patient, "video-desktop")
        doctor.goto(url + "/clinical-tools?patient_id=1")
        expect(doctor.locator(".tool-tile")).to_have_count(9)
        capture(doctor, "tools-desktop")
        doctor.get_by_role("button", name="Назначить HCL-33", exact=True).click()
        patient.goto(url + "/patients/1/profile/assessments")
        patient.locator(".assessment-row").filter(has_text="HCL-33").get_by_role(
            "link"
        ).click()
        patient.locator('input[name="answer_1"][value="0"]').check()
        patient.get_by_role("button", name="Продолжить без видео", exact=True).click()
        expect(patient.locator('input[name="answer_1"][value="0"]')).to_be_checked()
        expect(
            patient.get_by_text(
                "Вы выбрали прохождение без видеозаписи. Решение можно изменить."
            )
        ).to_be_visible()
        with patient.expect_popup() as popup:
            patient.get_by_role("link", name="Открыть окно записи", exact=True).click()
        camera = popup.value
        camera.on("pageerror", lambda e: errors.append(str(e)))
        expect(camera.locator("[data-camera-start]")).to_be_disabled()
        camera.locator("[data-camera-consent]").check()
        camera.locator("[data-camera-start]").click()
        expect(camera.locator("[data-camera-status]")).to_contain_text(
            "Идёт видеозапись"
        )
        patient.locator('input[name="answer_1"][value="0"]').check()
        patient.get_by_role("button", name="Сохранить и продолжить", exact=True).click()
        assert "item=2" in patient.url
        expect(camera.locator("[data-camera-status]")).to_contain_text(
            "Идёт видеозапись"
        )
        camera.wait_for_timeout(1700)
        camera.locator("[data-camera-stop]").click()
        expect(camera.locator("[data-camera-preview]")).to_be_visible()
        camera.locator("[data-camera-save]").click()
        expect(camera.locator(".recording-list video")).to_have_count(1)
        camera.wait_for_function(
            "document.querySelector('.recording-list video')?.readyState >= 1"
        )
        capture(camera, "camera-desktop")
        doctor.goto(patient.url.split("?")[0])
        expect(doctor.locator(".recording-list video")).to_have_count(1)
        camera.get_by_role("button", name="Удалить видеофрагмент 1", exact=True).click()
        expect(camera.locator(".recording-list video")).to_have_count(0)
        camera.locator("[data-camera-consent]").check()
        camera.locator("[data-camera-start]").click()
        expect(camera.locator("[data-camera-status]")).to_contain_text(
            "Идёт видеозапись"
        )
        patient.reload()
        patient.get_by_role(
            "button", name="Отозвать согласие на видео", exact=True
        ).click()
        expect(camera.locator("[data-camera-status]")).to_contain_text(
            "Согласие отозвано", timeout=10000
        )
        expect(camera.locator("[data-camera-live]")).to_be_hidden()
        expect(camera.locator("[data-camera-preview]")).to_be_hidden()
        camera.close()
        doctor.goto(url + "/medicines")
        expect(
            doctor.get_by_role("heading", name="Библиотека препаратов", exact=True)
        ).to_be_visible()
        expect(
            doctor.get_by_role("link", name="Экстренная помощь", exact=True)
        ).to_have_count(0)
        capture(doctor, "medicines-desktop")
        guest_context = browser.new_context(viewport={"width": 390, "height": 844})
        guest = guest_context.new_page()
        guest.on("pageerror", lambda e: errors.append(str(e)))
        guest.goto(url + "/faq")
        guest.get_by_label("Ваш вопрос", exact=True).fill(
            "Учебный анонимный вопрос для FAQ"
        )
        guest.get_by_label("Понимаю, что вопрос и ответ").check()
        guest.get_by_role("button", name="Задать анонимный вопрос").click()
        thread_url = guest.url
        expect(guest.get_by_role("status")).to_contain_text("Вопрос принят")
        doctor.goto(url + "/doctor/faq")
        doctor.get_by_label("Проверенный общий ответ").fill(
            "Учебный публичный ответ команды"
        )
        doctor.get_by_role("button", name="Опубликовать вопрос и ответ").click()
        guest.goto(thread_url)
        expect(
            guest.get_by_text("Учебный публичный ответ команды", exact=True)
        ).to_be_visible()
        guest.goto(url + "/faq")
        capture(guest, "faq-forum-mobile")
        doctor.goto(url + "/patients/1")
        capture(doctor, "specialist-desktop")
        doctor.set_viewport_size({"width": 390, "height": 844})
        capture(doctor, "specialist-mobile")
        assert not errors, errors
        from scripts.browser_diary import check_diary

        check_diary(patient, doctor, url)
        from scripts.browser_e02 import check_interface

        check_interface(patient, doctor, url)
        from scripts.browser_medicines import check_medicines

        check_medicines(patient, doctor, url)
        browser.close()
    print(
        "UI check passed: separate portals, trust/FAQ, teaching safety adaptation with hidden branches, stepped PHQ-4/9/GAD-7, PHQ-ADS assignment, safety events, synthetic audio, diary comments, separate profile pages, family tree/tabs, retired reports and a single family tree, anonymous moderated FAQ, diary notes, consultation booking/video link, desktop/mobile; no JS errors."
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
