"""Browser acceptance on caller-owned, disposable synthetic test accounts."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect


def check_clinician_forum(doctor, colleague, patient, url):
    out = Path("artifacts/doctor-forum")
    out.mkdir(parents=True, exist_ok=True)
    errors, audits = [], []
    for page in (doctor, colleague, patient):
        page.on("pageerror", lambda e: errors.append(str(e)))
    doctor.goto(url + "/doctor/tasks")
    doctor.get_by_role("link", name="Открыть форум врачей", exact=True).click()
    expect(
        doctor.get_by_role("heading", name="Форум врачей", exact=True)
    ).to_be_visible()
    expect(
        doctor.get_by_role("heading", name="Начните обсуждение с коллегами", exact=True)
    ).to_be_visible()
    doctor.get_by_role("link", name="+ Новая тема", exact=True).click()
    doctor.get_by_label("Что хотите обсудить?", exact=True).select_option("case")
    doctor.get_by_label("Заголовок", exact=True).fill("Обсуждение трудностей со сном")
    doctor.get_by_label("Описание и вопрос к коллегам", exact=True).fill(
        "Синтетический пример: почта test@example.org"
    )
    doctor.locator('input[name="deidentified"]').check()
    doctor.get_by_role("button", name="Предпросмотр публикации", exact=True).click()
    expect(doctor.get_by_role("alert")).to_contain_text("электронную почту")
    body = "Женщина 30–40 лет. Трудно засыпать перед рабочими днями в последние несколько недель. Какие обстоятельства стоит уточнить на встрече?"
    doctor.get_by_label("Описание и вопрос к коллегам", exact=True).fill(body)
    doctor.locator('input[name="deidentified"]').check()
    doctor.get_by_role("button", name="Предпросмотр публикации", exact=True).click()
    expect(doctor.locator(".forum-preview")).to_contain_text(body)
    colleague.goto(url + "/doctor/forum")
    expect(colleague.locator(".forum-feed")).not_to_contain_text(
        "Обсуждение трудностей со сном"
    )
    doctor.get_by_role("button", name="Опубликовать тему", exact=True).click()
    thread = doctor.url
    expect(
        doctor.get_by_role("heading", name="Обсуждение трудностей со сном", exact=True)
    ).to_be_visible()
    colleague.goto(thread)
    colleague.get_by_label("Ваш ответ коллегам", exact=True).fill(
        "Можно уточнить режим сна и вечернюю нагрузку."
    )
    colleague.locator('input[name="deidentified"]').check()
    colleague.get_by_role("button", name="Предпросмотр публикации", exact=True).click()
    colleague.get_by_role("button", name="Опубликовать ответ", exact=True).click()
    doctor.reload()
    expect(doctor.locator(".forum-comment")).to_contain_text("вечернюю нагрузку")
    doctor.locator(".forum-thread").get_by_role(
        "link", name="Изменить", exact=True
    ).click()
    doctor.get_by_label("Описание и вопрос к коллегам", exact=True).fill(
        body + " Дополнение: жалобы обсудили на консультации."
    )
    doctor.locator('input[name="deidentified"]').check()
    doctor.get_by_role("button", name="Предпросмотр публикации", exact=True).click()
    doctor.get_by_role("button", name="Опубликовать тему", exact=True).click()
    expect(doctor.locator(".forum-thread")).to_contain_text("Дополнение")
    assert patient.request.get(thread).status == 403
    assert patient.request.get(url + "/doctor/forum").status == 403
    assert body not in patient.request.get(url + "/faq").text()
    for name, route in (
        ("thread", thread),
        ("feed", url + "/doctor/forum"),
        ("compose", url + "/doctor/forum/new"),
    ):
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
    colleague.goto(thread)
    topic = colleague.locator(".forum-thread")
    topic.get_by_text("Заметили персональные данные?", exact=True).click()
    topic.locator('input[name="confirmed"]').check()
    topic.get_by_role(
        "button", name="Скрыть из-за персональных данных", exact=True
    ).click()
    colleague.goto(thread)
    expect(colleague.locator("main")).to_contain_text("Обсуждение недоступно")
    doctor.goto(url + "/doctor/forum?mine=true")
    expect(doctor.locator(".forum-feed")).to_contain_text("Скрыто")
    doctor.get_by_role("link", name="Обсуждение трудностей со сном", exact=True).click()
    doctor.locator(".forum-thread").get_by_role(
        "link", name="Изменить", exact=True
    ).click()
    doctor.get_by_label("Описание и вопрос к коллегам", exact=True).fill(
        "Обобщённый синтетический случай: трудности со сном перед рабочими днями."
    )
    doctor.locator('input[name="deidentified"]').check()
    doctor.get_by_role("button", name="Предпросмотр публикации", exact=True).click()
    doctor.get_by_role("button", name="Опубликовать тему", exact=True).click()
    colleague.goto(thread)
    expect(colleague.locator(".forum-thread")).to_contain_text(
        "Обобщённый синтетический случай"
    )
    assert not errors, errors
    (out / "accessibility.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "PASS clinician forum: actual create/preview/privacy rejection/publish/comment/edit/reload/hide/correct; two doctors, patient 403 and public FAQ isolation; 320/390/768/1280; 12 axe audits; no overflow or JavaScript errors.",
        flush=True,
    )
