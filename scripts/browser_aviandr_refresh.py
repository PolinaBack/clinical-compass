"""Actual patient submissions refresh an already open doctor page; synthetic DB only."""

import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

from playwright.sync_api import expect


def check_aviandr_refresh(doctor, second, patient, url):
    out = Path("artifacts/aviandr-refresh")
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    doctor.on("pageerror", lambda error: errors.append(str(error)))
    doctor.goto(url + "/patients/1/ai-analysis#aviandr")
    block = doctor.locator("[data-aviandr-analysis]")
    form = block.locator('form[action$="/aviandr/facts"]')
    form.locator("..").locator(":scope > summary").click()
    form.locator('[name="note"]').fill(
        "Несохранённый текст врача должен остаться после обновления."
    )
    doctor.locator("#av-details-indications > summary").click()
    with sqlite3.connect(os.environ["COMPASS_DB"]) as c:
        version = c.execute(
            "SELECT version FROM assessments WHERE patient_id=1 AND scale='phq4' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        day = now[:10]
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(1,?,?,?)",
            (day, day, now),
        ).lastrowid
        aid = c.execute(
            "INSERT INTO assessments(session_id,patient_id,scale,version,status) VALUES(?,1,'phq4',?,'draft')",
            (sid, version),
        ).lastrowid
        jobs_before = c.execute("SELECT count(*) FROM aviandr_runs").fetchone()[0]
    credentials = json.loads(os.environ["COMPASS_BROWSER_CREDENTIALS"])
    entry = doctor.context.browser.new_page()
    entry.goto(url + "/login/patient")
    entry.locator('[name="username"]').fill("patient-c")
    entry.locator('[name="password"]').fill(credentials["patient-c"])
    entry.locator("main button[type=submit],main button.full").click()
    entry.goto(f"{url}/assessments/{aid}")
    entry.locator('[name="answer_1"][value="3"]').check()
    entry.get_by_role("button", name="Сохранить и сделать паузу", exact=True).click()
    expect(block.locator("#av-indications [data-conclusion]")).to_contain_text(
        "PHQ-4 — 1/4 ответов", timeout=15000
    )
    entry.get_by_role("button", name="Сохранить и продолжить", exact=True).click()
    for item in (2, 3, 4):
        entry.locator(f'[name="answer_{item}"][value="0"]').check()
        entry.get_by_role(
            "button",
            name="Завершить и отправить" if item == 4 else "Сохранить и продолжить",
            exact=True,
        ).click()
    expect(block.locator("#av-indications [data-conclusion]")).to_contain_text(
        "PHQ-4: 6 → 3", timeout=15000
    )
    expect(block.locator("#av-indications [data-conclusion]")).not_to_contain_text(
        "PHQ-4 — 1/4 ответов"
    )
    expect(block.locator("#av-details-indications")).to_have_attribute("open", "")
    expect(form.locator('[name="note"]')).to_have_value(
        "Несохранённый текст врача должен остаться после обновления."
    )
    # A second doctor tab saves a real observation while the first keeps its draft.
    colleague_context = doctor.context.browser.new_context()
    colleague_context.add_cookies(doctor.context.cookies())
    colleague = colleague_context.new_page()
    colleague.goto(url + "/patients/1/ai-analysis")
    other = colleague.locator('form[action$="/aviandr/facts"]')
    other.locator("..").locator(":scope > summary").click()
    other.locator('[name="kind"]').select_option("age")
    other.locator('[name="value"]').fill("48")
    other.locator('[name="confirmed"]').check()
    other.locator('[name="note"]').fill(
        "Синтетическое уточнение возраста из второго окна врача."
    )
    other.get_by_role("button", name="Сохранить сведение", exact=True).click()
    expect(block.locator("#av-contraindications [data-conclusion]")).to_contain_text(
        "48", timeout=15000
    )
    expect(form.locator('[name="note"]')).to_have_value(
        "Несохранённый текст врача должен остаться после обновления."
    )
    expect(block.locator("[data-av-live-status]")).to_contain_text("обновлены")
    # Fact links must still work after replacing the live HTML.
    block.locator("#av-contraindications [data-basis] a").first.click()
    expect(block.locator("#av-evidence-P-contraindications-age")).to_be_visible()
    doctor.screenshot(path=str(out / "refreshed-1280.png"))
    with sqlite3.connect(os.environ["COMPASS_DB"]) as c:
        assert (
            c.execute("SELECT count(*) FROM aviandr_runs").fetchone()[0] == jobs_before
        )
        c.execute("UPDATE patients SET doctor_id=2 WHERE id=1")
    expect(block.locator("[data-av-live-status]")).to_contain_text(
        "Автообновление остановлено", timeout=15000
    )
    expect(form.locator('[name="note"]')).to_have_value(
        "Несохранённый текст врача должен остаться после обновления."
    )
    colleague_context.close()
    entry.close()
    assert not errors, errors
    (out / "browser.json").write_text(
        json.dumps(
            dict(
                real_http=True,
                synthetic_only=True,
                partial_and_completed_phq4=True,
                doctor_fact_from_other_tab=True,
                unsaved_form_and_details_preserved=True,
                fact_links_after_refresh=True,
                revoked_scope_stops_polling=True,
                no_model_job_started=True,
                js_errors=errors,
            ),
            indent=2,
        )
        + "\n"
    )
    print(
        "PASS real patient draft/completion of PHQ-4 and clinician observation from second tab update existing cards automatically; editor draft/details/fact links preserved, no model job, revoked access stops polling, no JS errors.",
        flush=True,
    )
