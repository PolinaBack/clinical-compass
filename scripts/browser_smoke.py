"""Optional browser test. Start the app with a disposable database first."""

import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def run(url, executable=None):
    with sync_playwright() as p:
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
            options["args"] = ["--no-sandbox"]
        browser = p.chromium.launch(**options)
        patient_context = browser.new_context(viewport={"width": 1280, "height": 900})
        doctor_context = browser.new_context(viewport={"width": 1280, "height": 900})
        patient = patient_context.new_page()
        errors = []
        patient.on("pageerror", lambda e: errors.append(str(e)))
        patient.goto(url)
        patient.get_by_role("button", name="Открыть кабинет").nth(0).click()
        patient.get_by_label("Самочувствие, 0–10").fill("7")
        patient.get_by_label("Заметка", exact=True).fill(
            "Синтетическая браузерная запись"
        )
        patient.get_by_label("Выполнение назначения врача").select_option("missed")
        patient.get_by_role("button", name="Сохранить запись").click()
        expect(
            patient.get_by_text("Синтетическая браузерная запись", exact=True)
        ).to_be_visible()
        patient.get_by_label("Субъективное напряжение").fill("3")
        patient.get_by_label("Субъективные трудности").fill("2")
        patient.get_by_role("button", name="Отправить врачу", exact=True).click()
        patient.get_by_label("Вопрос врачу").fill("Браузерный демовопрос")
        patient.get_by_role("button", name="Отправить", exact=True).click()
        patient.get_by_label("Описание синтетического события").fill(
            "Браузерный демосигнал"
        )
        patient.get_by_label("Прошу срочного внимания").check()
        patient.get_by_role("button", name="Сообщить врачу").click()
        patient.get_by_label("Вымышленное имя").fill("Демо-родитель")
        patient.get_by_label("Отношение к пациенту").select_option("mother")
        patient.get_by_role("button", name="Добавить участника").click()
        parent = patient.locator("select[name=parent_id] option").last.get_attribute(
            "value"
        )
        patient.get_by_label("Вымышленное имя").fill("Демо-ребёнок")
        patient.get_by_label("Отношение к пациенту").select_option("child")
        patient.get_by_label("Родитель этого участника").select_option(parent)
        patient.get_by_role("button", name="Добавить участника").click()
        expect(patient.locator("#genogram svg")).to_be_visible()
        expect(patient.locator("#genogram svg line")).to_have_count(1)
        doctor = doctor_context.new_page()
        doctor.on("pageerror", lambda e: errors.append(str(e)))
        doctor.goto(url)
        doctor.get_by_role("button", name="Открыть кабинет").nth(2).click()
        expect(doctor.locator("#live-alerts")).to_contain_text("Браузерный демосигнал")
        doctor.get_by_role("link", name="Карточка пациента").nth(0).click()
        expect(doctor.get_by_text("Браузерный демовопрос", exact=True)).to_be_visible()
        doctor.get_by_role(
            "button", name="Подтвердить просмотр текущих событий"
        ).click()
        saved_alerts = doctor.request.get(url + "/api/alerts").json()["alerts"]
        assert saved_alerts and all(item["acknowledged"] for item in saved_alerts)
        doctor.get_by_label("Рекомендация / ответ врача").fill("Браузерный демоответ")
        doctor.get_by_role("button", name="Отправить", exact=True).click()
        doctor.locator("select[name=category]").select_option("recommended")
        doctor.get_by_label("Решение", exact=True).fill(
            "Синтетическое решение для теста интерфейса"
        )
        doctor.get_by_label("Источник и основание").fill(
            "Синтетический тест, не клиническое правило"
        )
        doctor.get_by_label("Подтверждаю решение как врач").check()
        doctor.get_by_role("button", name="Подтвердить и сохранить").click()
        patient.reload()
        expect(patient.get_by_text("Браузерный демоответ", exact=True)).to_be_visible()
        expect(
            patient.get_by_text("ЖЕЛАТЕЛЬНО: направление к психиатру", exact=True)
        ).to_be_visible()
        Path("artifacts").mkdir(exist_ok=True)
        patient.screenshot(path="artifacts/patient-desktop.png", full_page=True)
        patient.set_viewport_size({"width": 390, "height": 844})
        patient.screenshot(path="artifacts/patient-mobile.png", full_page=True)
        assert patient.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), "Mobile horizontal overflow"
        assert not errors, errors
        browser.close()
    print(
        "Browser smoke passed: diary, questionnaire, messages, alerts, family SVG, confirmed decision, mobile overflow; no JS errors. Microphone not tested."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--executable", default=None)
    args = parser.parse_args()
    run(args.url, args.executable)
