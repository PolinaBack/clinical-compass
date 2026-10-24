"""Optional Chromium acceptance: disposable DBs, explicit mock success, real refusal.

The mock success is labelled in the UI and is not evidence of Ollama quality.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright


def run(app_python, executable=None):
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts/experimental-ai"
    output.mkdir(parents=True, exist_ok=True)
    app_python = str(Path(app_python).absolute())
    with TemporaryDirectory(prefix="compass-ai-browser-") as folder:
        work = Path(folder)
        env = {
            **os.environ,
            "COMPASS_DB": str(work / "clinical.db"),
            "COMPASS_EXPERIMENT_DB": str(work / "experiment.db"),
            "COMPASS_EXPERIMENT_SOURCES": str(work / "sources"),
            "COMPASS_EXPERIMENT_OLLAMA_URL": "http://127.0.0.1:1",
            "COMPASS_EXPERIMENT_OLLAMA_MODEL": "qwen2.5:3b",
            "BROWSER_CREDS": str(work / "credentials.json"),
        }

        def command(code):
            subprocess.run(
                [app_python, "-c", code],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
            )

        command(
            """import json,os
from scripts.setup_demo import setup
from app.experimental_ai.store import initialize
from pathlib import Path
credentials = dict(setup())
Path(os.environ['BROWSER_CREDS']).write_text(json.dumps(credentials))
initialize()
"""
        )
        credentials = json.loads((work / "credentials.json").read_text())
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (work / "server.log").open("w") as log:
            proc = subprocess.Popen(
                [
                    app_python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(100):
                    try:
                        if opener.open(base, timeout=1).status == 200:
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Temporary app did not start")
                errors, audits = [], []
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        **(
                            {"executable_path": executable, "args": ["--no-sandbox"]}
                            if executable
                            else {}
                        )
                    )
                    doctor_context = browser.new_context(
                        viewport=dict(width=1280, height=900)
                    )
                    patient_context = browser.new_context(
                        viewport=dict(width=1280, height=900)
                    )
                    doctor, patient = (
                        doctor_context.new_page(),
                        patient_context.new_page(),
                    )
                    for page, portal, username in [
                        (doctor, "specialist", "specialist"),
                        (patient, "patient", "patient-a"),
                    ]:
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/login/" + portal)
                        page.get_by_label("Логин", exact=True).fill(username)
                        page.get_by_label("Пароль", exact=True).fill(
                            credentials[username]
                        )
                        page.locator("main form button").click()
                    doctor.get_by_role(
                        "link", name="Открыть эксперимент", exact=True
                    ).click()
                    expect(
                        doctor.get_by_text(
                            "Тестовый режим · только синтетические данные", exact=True
                        )
                    ).to_be_visible()
                    expect(
                        doctor.get_by_role("heading", name="Мария Соколова", exact=True)
                    ).to_be_visible()
                    assert (
                        patient.request.get(base + "/doctor/experimental-ai").status
                        == 403
                    )
                    expect(
                        patient.locator('a[href="/doctor/experimental-ai"]')
                    ).to_have_count(0)
                    doctor.get_by_role(
                        "button", name="Запустить анализ в Ollama", exact=True
                    ).click()
                    run_id = doctor.locator("#experiment-progress").get_attribute(
                        "data-run-id"
                    )
                    assert (
                        doctor.request.get(
                            base + f"/api/experimental-ai/runs/{run_id}"
                        ).json()["status"]
                        == "queued"
                    )
                    command(
                        "from app.experimental_ai.jobs import process_one; from tests.test_experimental_ai import provider; assert process_one(provider=provider())"
                    )
                    expect(doctor.locator("[data-run-status-label]")).to_have_text(
                        "Завершён", timeout=20000
                    )
                    doctor.get_by_role(
                        "link", name="Обновить результат запуска", exact=True
                    ).click()
                    expect(doctor.locator("#ai-result")).to_contain_text(
                        "ТЕХНИЧЕСКАЯ ФИКСТУРА"
                    )
                    expect(
                        doctor.get_by_role(
                            "heading",
                            name="Индивидуальные уточняющие вопросы",
                            exact=True,
                        )
                    ).to_be_visible()
                    for field in ("grounding", "usefulness", "safety"):
                        doctor.locator(f'[name="{field}"]').select_option("2")
                    doctor.get_by_role(
                        "button", name="Сохранить оценку эксперимента", exact=True
                    ).click()
                    expect(doctor.locator("#expert-review")).to_contain_text(
                        "Сохранено оценок: 1"
                    )
                    exported = doctor.request.get(
                        base + f"/doctor/experimental-ai/export/{run_id}"
                    ).json()
                    assert (
                        exported["run"]["provenance"]["provider"]
                        == "mock-http-test-only"
                    )
                    assert len(exported["dataset"]["observations"]) == 30
                    assert (
                        patient.request.get(
                            base + f"/doctor/experimental-ai/export/{run_id}"
                        ).status
                        == 403
                    )

                    # The successful fixture never touches the real default experiment DB.
                    for width in (320, 390, 768, 1280):
                        doctor.set_viewport_size(dict(width=width, height=900))
                        doctor.goto(base + f"/doctor/experimental-ai?run_id={run_id}")
                        assert doctor.evaluate(
                            "document.documentElement.scrollWidth<=innerWidth+1"
                        ), width
                        doctor.screenshot(
                            path=str(output / f"mock-labelled-{width}.png"),
                            full_page=True,
                        )
                        if os.getenv("COMPASS_AXE_PATH"):
                            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                            violations = doctor.evaluate(
                                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                            )
                            audits.append(
                                dict(
                                    width=width,
                                    state="labelled-mock-success",
                                    violations=violations,
                                )
                            )
                            assert not violations, audits[-1]

                    # Actual HTTP connection refusal: no canned failure provider.
                    doctor.get_by_role(
                        "button", name="Запустить анализ в Ollama", exact=True
                    ).click()
                    command(
                        "from app.experimental_ai.jobs import process_one; assert process_one()"
                    )
                    expect(doctor.locator("[data-run-status-label]")).to_have_text(
                        "Ошибка", timeout=20000
                    )
                    doctor.get_by_role(
                        "link", name="Обновить результат запуска", exact=True
                    ).click()
                    expect(doctor.locator("[data-run-error]")).to_contain_text(
                        "Нет соединения"
                    )
                    expect(doctor.locator("#ai-result")).to_have_count(0)
                    expect(
                        doctor.get_by_role(
                            "heading", name="Программный анализ динамики", exact=True
                        )
                    ).to_be_visible()
                    for width in (390, 1280):
                        doctor.set_viewport_size(dict(width=width, height=900))
                        doctor.screenshot(
                            path=str(output / f"real-unavailable-{width}.png"),
                            full_page=True,
                        )
                        if os.getenv("COMPASS_AXE_PATH"):
                            doctor.add_script_tag(path=os.environ["COMPASS_AXE_PATH"])
                            violations = doctor.evaluate(
                                """async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))"""
                            )
                            audits.append(
                                dict(
                                    width=width,
                                    state="real-unavailable",
                                    violations=violations,
                                )
                            )
                            assert not violations, audits[-1]
                    csrf = doctor.locator('input[name="csrf"]').first.input_value()
                    assert (
                        doctor.request.post(
                            base + "/doctor/experimental-ai/run",
                            form=dict(csrf=csrf, patient_id="1"),
                        ).status
                        == 422
                    )
                    assert not errors, errors
                    browser.close()
                (output / "accessibility.json").write_text(
                    json.dumps(audits, ensure_ascii=False, indent=2)
                )
                print(
                    "PASS experimental UI: isolated 30-day scenario; doctor-only, no real-patient input; queue/poll/history/export/expert score; mock success explicitly labelled; REAL HTTP refusal and metrics without fake summary; 320/390/768/1280; no overflow/JS errors; "
                    + str(len(audits))
                    + " axe checks. This is not a real model-quality test."
                )
            finally:
                proc.terminate()
                proc.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-python", default=sys.executable)
    parser.add_argument("--executable")
    args = parser.parse_args()
    run(args.app_python, args.executable)
