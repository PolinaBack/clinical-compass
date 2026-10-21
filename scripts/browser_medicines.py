"""Library acceptance. Only run against disposable synthetic demo databases."""

import json
import os
import io
from datetime import date, timedelta
from pathlib import Path
import openpyxl
from playwright.sync_api import expect


def check_medicines(patient, doctor, url):
    errors = []
    doctor.on("pageerror", lambda error: errors.append(str(error)))
    root = Path(__file__).resolve().parents[1]
    doctor.goto(url + "/medicines")
    expect(
        doctor.get_by_role("heading", name="Библиотека препаратов", exact=True)
    ).to_be_visible()
    query = doctor.get_by_label("Название, МНН или действующее вещество", exact=True)
    query.fill("мариту")
    expect(doctor.locator("#medicine-suggestions option")).to_have_count(1)
    expect(doctor.locator("#medicine-suggestions option")).to_have_attribute(
        "value", "Авиандр"
    )
    query.fill("эсциталопрам")
    doctor.get_by_role("button", name="Найти", exact=True).click()
    expect(doctor.locator(".medicine-catalog a")).to_have_count(1)
    expect(doctor.locator(".medicine-catalog a")).to_contain_text("Ципралекс")
    doctor.goto(url + "/medicines/aviandr")
    expect(
        doctor.get_by_text("Подтверждённых данных в нашей базе нет.", exact=False).first
    ).to_be_visible()
    doctor.goto(url + "/medicines/combinations")
    for code in ("D01", "D02", "D04"):
        doctor.locator(f'input[name="drug"][value="{code}"]').check()
    doctor.get_by_role("button", name="Проверить сочетания", exact=True).click()
    expect(doctor.locator(".medicine-pair")).to_have_count(3)
    expect(
        doctor.get_by_text(
            "Подтверждённое взаимодействие не найдено в нашей базе.", exact=False
        )
    ).to_have_count(3)
    doctor.goto(url + "/medicines/import")
    form = doctor.locator("form").filter(has=doctor.locator('input[value="excel"]'))
    form.locator('input[type="file"]').set_input_files(
        root / "docs/inputs/2026-10-21/Библиотека препаратов.xlsx"
    )
    form.get_by_role("button", name="Предпросмотр Excel", exact=True).click()
    expect(
        doctor.get_by_role("heading", name="Предварительный просмотр", exact=False)
    ).to_be_visible()
    expect(
        doctor.get_by_text("Такой файл уже импортирован:", exact=False)
    ).to_be_visible()
    doctor.get_by_label("Подтверждаю добавление / обновление", exact=False).check()
    doctor.get_by_role("button", name="Подтвердить импорт", exact=True).click()
    expect(
        doctor.get_by_role("heading", name="Отчёт об импорте", exact=False)
    ).to_be_visible()
    report_path = doctor.url.removeprefix(url)
    expect(doctor.get_by_text("Новых: 0; обновлений: 0;", exact=False)).to_be_visible()
    # Synthetic evidence stays solely in the disposable DB; never a medical source.
    w = openpyxl.Workbook()
    sheet = w.active
    sheet.title = "Сведения"
    sheet.append(["Поле", "Текст", "Раздел/страница"])
    sheet.append(
        ["warnings", "СИНТЕТИЧЕСКИЙ ТЕКСТ ДЛЯ БРАУЗЕРНОГО ТЕСТА", "Тестовый раздел"]
    )
    w.create_sheet("Взаимодействия").append(
        ["Препарат A", "Препарат B", "Уровень", "Объяснение", "Раздел/страница"]
    )
    data = io.BytesIO()
    w.save(data)
    doctor.goto(url + "/medicines/import")
    form = doctor.locator("form").filter(has=doctor.locator('input[value="official"]'))
    form.get_by_label("Файл PDF или XLSX, до 8 МБ", exact=True).set_input_files(
        {
            "name": "synthetic-browser.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "buffer": data.getvalue(),
        }
    )
    for label, value in [
        (
            "Ссылка на конкретный документ / запись ГРЛС",
            "https://grls.rosminzdrav.ru/TEST-NOT-A-REAL-SOURCE",
        ),
        ("Версия / дата редакции документа", "СИНТЕТИЧЕСКАЯ ВЕРСИЯ — только тест"),
        ("Номер регистрационного удостоверения (РУ)", "TEST-RU"),
        (
            "Точный торговый продукт, производитель, форма и дозировка",
            "СИНТЕТИЧЕСКИЙ ПРОДУКТ — тест",
        ),
        ("Дата сверки в ГРЛС", date.today().isoformat()),
        (
            "Повторная сверка не позднее",
            (date.today() + timedelta(days=30)).isoformat(),
        ),
    ]:
        form.get_by_label(label, exact=True).fill(value)
    form.get_by_role(
        "button", name="Предпросмотр официального документа", exact=True
    ).click()
    doctor.get_by_label("Подтверждаю добавление / обновление", exact=False).check()
    doctor.get_by_role("button", name="Подтвердить импорт", exact=True).click()
    source_path = doctor.url.removeprefix(url)
    doctor.get_by_label("Я сверил действующую запись", exact=False).check()
    doctor.get_by_label("Обоснование / разрешение разногласий", exact=True).fill(
        "СИНТЕТИЧЕСКАЯ ПРОВЕРКА ПРОГРАММЫ, не реальная сверка ГРЛС"
    )
    doctor.get_by_role(
        "button", name="Сохранить решение о проверке", exact=True
    ).click()
    doctor.goto(url + "/medicines/aviandr")
    expect(
        doctor.get_by_text("СИНТЕТИЧЕСКИЙ ТЕКСТ ДЛЯ БРАУЗЕРНОГО ТЕСТА", exact=True)
    ).to_be_visible()
    doctor.goto(url + source_path)
    doctor.get_by_label("Действие", exact=True).select_option("withdraw")
    doctor.get_by_label("Обоснование / разрешение разногласий", exact=True).fill(
        "Отзыв синтетического источника после проверки программы"
    )
    doctor.get_by_role(
        "button", name="Сохранить решение о проверке", exact=True
    ).click()
    doctor.goto(url + "/medicines/aviandr")
    expect(
        doctor.get_by_text("СИНТЕТИЧЕСКИЙ ТЕКСТ ДЛЯ БРАУЗЕРНОГО ТЕСТА", exact=True)
    ).to_have_count(0)
    # Keyboard search remains usable without relying on a pointer or suggestions.
    doctor.goto(url + "/medicines")
    doctor.get_by_label("Название, МНН или действующее вещество", exact=True).focus()
    doctor.keyboard.type("Авиандр")
    doctor.keyboard.press("Enter")
    expect(doctor.locator(".medicine-catalog a")).to_have_count(1)
    # Patient and emergency paths remain independent of this physician reference.
    denied = patient.goto(url + "/medicines")
    assert denied.status == 403
    patient.goto(url + "/patients/1/diary")
    expect(patient.locator('[data-open="wellbeing-dialog"]')).to_be_visible()
    audits = []
    axe = os.getenv("COMPASS_AXE_PATH")
    paths = [
        "/medicines",
        "/medicines/aviandr",
        "/medicines/import",
        "/medicines/combinations?drug=D01&drug=D02&drug=D04",
        "/medicines/scenarios",
        report_path,
        source_path,
    ]
    for width in (320, 390, 768, 1280):
        doctor.set_viewport_size(dict(width=width, height=900))
        for i, path in enumerate(paths):
            assert doctor.goto(url + path).status == 200
            assert doctor.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), (width, path)
            if width in (390, 1280):
                if axe:
                    doctor.add_script_tag(path=axe)
                    violations = doctor.evaluate(
                        """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))"""
                    )
                    audits.append(dict(path=path, width=width, violations=violations))
                    Path("artifacts/medicines-accessibility.json").write_text(
                        json.dumps(audits, ensure_ascii=False, indent=2)
                    )
                if i in (0, 1, 3):
                    doctor.screenshot(
                        path=f"artifacts/medicines-{i}-{width}.png", full_page=True
                    )
    if axe:
        assert not any(a["violations"] for a in audits), audits
    assert not errors, errors
    print(
        "Library browser acceptance passed: search/INN/suggestions, 3 drugs/3 pairs, Excel preview/reimport, patient denied, 4 viewport widths."
    )
