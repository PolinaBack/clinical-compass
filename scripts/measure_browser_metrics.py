"""Browser measurements on a temporary synthetic server; no real accounts."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def browser_checks(base, output):
    from playwright.sync_api import sync_playwright, expect

    output.mkdir(parents=True, exist_ok=True)
    results = dict(
        synthetic=True,
        browser_checks=[],
        navigation=[],
        ergonomics=[],
        accessibility=[],
        page_errors=[],
        http_errors=[],
        urgent_delivery=[],
    )
    axe = Path("/tmp/compass-refinements-axe/node_modules/axe-core/axe.min.js")
    with sync_playwright() as p:
        results["bundled_browser_engines"] = {
            name: Path(getattr(p, name).executable_path).exists()
            for name in ["chromium", "firefox", "webkit"]
        }
        browser = p.chromium.launch(
            headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"]
        )
        results["browser_version"] = browser.version
        results["actual_engine"] = "chromium"
        results["browser_executable"] = "/usr/bin/chromium"
        doctor_context = browser.new_context(
            viewport=dict(width=1440, height=1000), reduced_motion="reduce"
        )
        patient_context = browser.new_context(
            viewport=dict(width=1440, height=1000), reduced_motion="reduce"
        )
        doctor, patient = doctor_context.new_page(), patient_context.new_page()
        for page in [doctor, patient]:
            page.on(
                "pageerror", lambda error: results["page_errors"].append(str(error))
            )
            page.on(
                "response",
                lambda response: (
                    results["http_errors"].append(
                        dict(url=response.url.split(base)[-1], status=response.status)
                    )
                    if response.status >= 500
                    else None
                ),
            )

        def login(page, name, portal):
            page.goto(base + "/login/" + portal)
            page.locator("[name=username]").fill(name)
            page.locator("[name=password]").fill(os.environ["COMPASS_METRIC_PASSWORD"])
            page.locator('form[action="/login"] button').click()
            page.wait_for_url(
                "**/doctor" if portal == "specialist" else "**/patients/1/diary"
            )

        login(doctor, "doctor", "specialist")
        login(patient, "patient1", "patient")
        # Count explicit browser actions; no human think/reading time is included.
        for iteration in range(3):
            patient.goto(base + "/patients/1/diary")
            start, actions = time.perf_counter(), 0
            patient.locator('[data-open="wellbeing-dialog"]').click()
            actions += 1
            patient.get_by_label("Самочувствие, 0–10", exact=True).fill("6")
            actions += 1
            patient.get_by_role("button", name="Применить оценку", exact=True).click()
            actions += 1
            patient.locator('[data-open="sleep-dialog"]').click()
            actions += 1
            patient.get_by_label("Продолжительность сна, часы", exact=True).fill("7.5")
            actions += 1
            patient.get_by_role("button", name="Применить сон", exact=True).click()
            actions += 1
            marker = f"SYNTHETIC BROWSER DIARY METRIC {iteration}"
            patient.get_by_label("Заметка", exact=True).fill(marker)
            actions += 1
            patient.get_by_role("button", name="Сохранить запись", exact=True).click()
            actions += 1
            expect(patient.locator("main")).to_contain_text(marker)
            results["ergonomics"].append(
                dict(
                    scenario="diary_wellbeing_sleep_note",
                    actions=actions,
                    elapsed_ms=round((time.perf_counter() - start) * 1000, 3),
                    saved=True,
                )
            )

        public = doctor_context.new_page()
        for label, page, paths in [
            ("public", public, ["/", "/login/patient", "/help-now"]),
            (
                "patient",
                patient,
                [
                    "/patients/1/diary",
                    "/patients/1/profile",
                    "/patients/1/profile/assessments",
                    "/patients/1/profile/extracts",
                ],
            ),
            (
                "doctor",
                doctor,
                [
                    "/doctor",
                    "/doctor/messages",
                    "/medicines",
                    "/doctor/diary-cards",
                    "/patients/1/ai-analysis",
                ],
            ),
        ]:
            for path in paths:
                for width in [320, 390, 768, 1440]:
                    page.set_viewport_size(dict(width=width, height=1000))
                    response = page.goto(base + path, wait_until="load")
                    page.evaluate("document.fonts.ready")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth>innerWidth+1"
                    )
                    results["browser_checks"].append(
                        dict(
                            role=label,
                            path=path,
                            width=width,
                            status=response.status,
                            overflow=overflow,
                        )
                    )
                    nav = page.evaluate(
                        "() => {const n=performance.getEntriesByType('navigation')[0];return {ttfb_ms:n.responseStart-n.requestStart,dom_content_loaded_ms:n.domContentLoadedEventEnd,load_ms:n.loadEventEnd}}"
                    )
                    results["navigation"].append(dict(path=path, width=width, **nav))
                    if axe.exists() and width in [390, 1440]:
                        page.add_script_tag(path=str(axe))
                        violations = page.evaluate(
                            "async () => (await axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>n.target)}))"
                        )
                        results["accessibility"].append(
                            dict(path=path, width=width, violations=violations)
                        )
                    if width == 390 and path in [
                        "/patients/1/diary",
                        "/patients/1/ai-analysis",
                    ]:
                        page.screenshot(
                            path=str(
                                output
                                / (
                                    "diary-390.png"
                                    if "diary" in path
                                    else "analysis-390.png"
                                )
                            ),
                            full_page=True,
                        )
        public.close()

        # Measure delivery with the doctor's tab already visible: no focus event
        # forces an immediate refresh, so this includes the normal polling delay.
        patient.goto(base + "/patients/1/safety")
        patient.locator('form[action="/safety/start"] button').click()
        for item, value in [(1, 0), (2, 1), (3, 0)]:
            patient.locator(f'[name=answer_{item}][value="{value}"]').check()
            patient.locator("#assessment-form button[name=intent][value=next]").click()
        start = time.perf_counter()
        patient.locator('[name=answer_4][value="1"]').check()
        expect(patient.locator("[data-safety-support]")).to_be_visible()
        results["local_help_ms"] = round((time.perf_counter() - start) * 1000, 3)
        form = patient.locator("#assessment-form").evaluate(
            "f => ({action:f.getAttribute('action') || location.pathname,data:Object.fromEntries(new FormData(f))})"
        )
        form["data"]["intent"] = "next"
        doctor.goto(base + "/doctor/messages")
        doctor.bring_to_front()
        doctor.wait_for_timeout(700)
        assert not doctor.evaluate("document.hidden")
        start = time.perf_counter()
        response = patient.request.post(
            base + form["action"], form=form["data"], max_redirects=0
        )
        if response.status != 303:
            raise RuntimeError(f"Critical answer save failed: {response.status}")
        saved = time.perf_counter()
        expect(doctor.locator("[data-safety-message-count]")).to_have_text(
            "1", timeout=15000
        )
        results["urgent_delivery"].append(
            dict(
                save_http_ms=round((saved - start) * 1000, 3),
                response_to_visible_ms=round((time.perf_counter() - saved) * 1000, 3),
                total_ms=round((time.perf_counter() - start) * 1000, 3),
                doctor_already_visible=True,
            )
        )
        expect(doctor.locator("[data-safety-messages]")).to_contain_text(
            "Результаты теста"
        )
        doctor.get_by_role("button", name="Подтвердить получение", exact=True).click()
        expect(doctor.locator("[data-safety-message-count]")).to_have_text("1")
        doctor.locator("[data-safety-action-form] textarea").fill(
            "СИНТЕТИЧЕСКАЯ ПРОВЕРКА: контакт и действия документированы."
        )
        doctor.get_by_role(
            "button", name="Сохранить действия врача", exact=True
        ).click()
        expect(doctor.locator("[data-safety-message-count]")).to_be_hidden()
        results["urgent_lifecycle"] = dict(
            results_visible=True,
            acknowledgement_keeps_pinned=True,
            documented_action_unpins=True,
        )
        browser.close()
    (output / "browser.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                pages=len(results["browser_checks"]),
                accessibility_scans=len(results["accessibility"]),
                page_errors=len(results["page_errors"]),
                overflows=sum(v["overflow"] for v in results["browser_checks"]),
                axe_violations=sum(
                    len(v["violations"]) for v in results["accessibility"]
                ),
            ),
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-child", action="store_true")
    parser.add_argument("--url")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.browser_child:
        browser_checks(args.url, output)
        return
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.storage import connect, now
    from tests.helpers import provision, TEST_PASSWORD

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compass-browser-metrics-") as folder:
        path = Path(folder) / "synthetic.db"
        with TestClient(create_app(path)) as client:
            provision(client)
            with connect(path) as c:
                c.execute(
                    "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(1,'SYNTHETIC-BROWSER',1,1,?)",
                    (now(),),
                )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(
            os.environ,
            COMPASS_DB=str(path),
            PYTHONPATH=str(Path.cwd()),
            COMPASS_METRIC_PASSWORD=TEST_PASSWORD,
        )
        with (output / "browser-server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(200):
                    try:
                        urllib.request.urlopen(base, timeout=1)
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Synthetic browser server unavailable")
                subprocess.run(
                    [
                        "python3",
                        str(Path(__file__).resolve()),
                        "--browser-child",
                        "--url",
                        base,
                        "--output",
                        str(output),
                    ],
                    env=env,
                    check=True,
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
