"""Additional E02 browser acceptance, called by browser_smoke on disposable data."""

import json
import os
import re
from pathlib import Path
from playwright.sync_api import expect


def check_interface(patient, doctor, url):
    patient.goto(url + "/settings")
    # Opening settings never enables any account-level media permission.
    expect(patient.locator('form[action="/settings/consultations"] input:checked')).to_have_count(0)
    form = patient.locator('form[action="/settings/interface"]')
    form.get_by_label("Без декора:", exact=False).check()
    form.get_by_label("Показывать «Общее самочувствие»", exact=True).uncheck()
    form.get_by_label("Закрепить «Консультации»", exact=True).check()
    form.get_by_role("button", name="Сохранить оформление", exact=True).click()
    patient.goto(url + "/me/overview")
    expect(patient.locator("body")).to_have_class(re.compile(r".*minimal-ui.*"))
    expect(patient.locator('[data-card="wellbeing"]')).to_have_count(0)
    assert patient.locator('[data-card]').first.get_attribute('data-card') == 'consultations'
    expect(patient.get_by_role("link", name="Экстренная помощь", exact=True)).to_be_visible()
    patient.goto(url + "/settings")
    form = patient.locator('form[action="/settings/interface"]')
    form.get_by_label("Без декора:", exact=False).uncheck()
    form.get_by_label("Показывать «Общее самочувствие»", exact=True).check()
    form.get_by_label("Закрепить «Консультации»", exact=True).uncheck()
    form.get_by_role("button", name="Сохранить оформление", exact=True).click()

    def tab_to(page, selector):
        for _ in range(160):
            page.keyboard.press('Tab')
            if page.evaluate("s=>document.activeElement.matches(s)", selector):
                return
        raise AssertionError('Keyboard could not reach ' + selector)

    # A complete keyboard path: main page → settings → decoration preference → save.
    patient.goto(url + '/me/overview')
    tab_to(patient, 'a.settings-link')
    patient.keyboard.press('Enter')
    expect(patient.get_by_role('heading', name='Настройки и конфиденциальность', exact=True)).to_be_visible()
    tab_to(patient, '.account-settings nav a[href="#interface"]')
    patient.keyboard.press('Enter')
    tab_to(patient, 'input[name="minimal"]')
    patient.keyboard.press('Space')
    tab_to(patient, 'form[action="/settings/interface"] button')
    patient.keyboard.press('Enter')
    expect(patient.locator('body')).to_have_class(re.compile(r'.*minimal-ui.*'))
    patient.locator('input[name="minimal"]').uncheck()
    patient.get_by_role('button', name='Сохранить оформление', exact=True).click()

    pages = [
        (patient, ["/me/overview", "/patients/1/diary", "/patients/1/results", "/me/materials",
                   "/patients/1/profile", "/patients/1/profile/family", "/patients/1/reports", "/settings", "/help-now"]),
        (doctor, ["/doctor", "/doctor/calendar", "/doctor/tasks", "/doctor/library",
                  "/patients/1", "/patients/1/reports", "/settings"]),
    ]
    axe_path = os.environ.get("COMPASS_AXE_PATH")
    audits = []
    for page, paths in pages:
        for width in (320, 390, 768, 1280):
            page.set_viewport_size(dict(width=width, height=900))
            page.emulate_media(reduced_motion="reduce")
            for path in paths:
                response = page.goto(url + path)
                assert response.status == 200, (path, response.status)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (path, width)
                assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
                assert page.locator('button:not([aria-label])').evaluate_all("els=>els.every(e=>e.textContent.trim().length>0)")
                if width == 390:
                    nav = page.locator('.portal-nav')
                    assert nav.evaluate("e=>getComputedStyle(e).position") == 'fixed'
                    assert nav.locator('a').evaluate_all("els=>els.every(e=>e.getBoundingClientRect().height>=44)")
                    if page == patient:
                        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        help_link = page.get_by_role('link', name='Экстренная помощь', exact=True)
                        assert help_link.evaluate('e=>{const r=e.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && r.height>=44}')
                        page.evaluate('window.scrollTo(0,0)')
                if axe_path and width in (390, 1280):
                    page.add_script_tag(path=axe_path)
                    results = page.evaluate("""async () => (await axe.run(document, {
                        runOnly: {type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
                    })).violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))""")
                    audits.append(dict(path=path, width=width, violations=results))
                    Path('artifacts/e02-accessibility.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2))
                if path in ('/me/overview', '/doctor'):
                    page.screenshot(path=f"artifacts/e02-{'patient' if page == patient else 'doctor'}-{width}.png", full_page=True)
        page.goto(url + ("/me/overview" if page == patient else "/doctor"))
        # Start the route with the keyboard and use its skip link.
        page.keyboard.press('Tab')
        assert page.evaluate("document.activeElement.classList.contains('skip-link')")
        page.keyboard.press('Enter')
        assert page.evaluate("location.hash") == '#main'
        tab_to(page, 'a[href="/privacy"]')
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        page.keyboard.press('Enter')
        page.wait_for_url(url + '/privacy')
    if axe_path:
        Path('artifacts/e02-accessibility.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2))
        violations = [a for a in audits if a['violations']]
        assert not violations, json.dumps(violations, ensure_ascii=False)
    print('PASS E02: real portal links and data, report relocation, durable UI controls, 320/390/768/1280px, keyboard and reduced motion')
