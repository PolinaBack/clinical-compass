"""Reproducible synthetic measurements; never opens the working patient DB.

Run after pytest has finished, on an otherwise idle host. This measures the local
MVP, not clinical model accuracy, a production capacity limit, or legal compliance.
"""

import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.safety import SAFETY_VERSION
from app.services import aviandr_analysis, safety_alerts
from app.services.clinical_forms import today
from app.storage import connect, now
from scripts.backup import backup
from scripts.setup_aviandr_scenarios import seed
from tests.helpers import provision, TEST_HASH, TEST_PASSWORD


def stats(values):
    values = sorted(values)
    if not values:
        return dict(n=0)

    def percentile(q):
        return values[max(0, __import__("math").ceil(len(values) * q) - 1)]

    return dict(
        n=len(values),
        mean_ms=round(statistics.mean(values), 3),
        p50_ms=round(percentile(0.5), 3),
        p95_ms=round(percentile(0.95), 3),
        p99_ms=round(percentile(0.99), 3),
        max_ms=round(values[-1], 3),
    )


def timed(call, n=30):
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000)
    return stats(samples)


def csrf(response):
    response.raise_for_status()
    match = re.search(r'name="csrf"\s+value="([^"]+)"', response.text)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', response.text)
    if not match:
        raise RuntimeError("Missing CSRF in synthetic test response")
    return match[1]


def sign_in(client, username="doctor", portal="specialist"):
    token = csrf(client.get("/login/" + portal))
    response = client.post(
        "/login",
        data=dict(username=username, password=TEST_PASSWORD, portal=portal, csrf=token),
    )
    if response.status_code not in (200, 303):
        raise RuntimeError(f"Synthetic login failed: {response.status_code}")


def add_urgent(path, pid=1):
    with connect(path) as c:
        sid = c.execute(
            "INSERT INTO assessment_sessions(patient_id,period_start,period_end,created_at) VALUES(?,?,?,?)",
            (pid, today().isoformat(), today().isoformat(), now()),
        ).lastrowid
        aid = c.execute(
            "INSERT INTO assessments(session_id,patient_id,scale,version,revision) VALUES(?,?,'safety6',?,1)",
            (sid, pid, SAFETY_VERSION),
        ).lastrowid
        answers = {
            str(k): dict(value=v, state="answered", comment="", discuss=0)
            for k, v in [(1, 0), (2, 1), (3, 0), (4, 1)]
        }
        c.executemany(
            "INSERT INTO answers(assessment_id,item,value,state,changed_at) VALUES(?,?,?,'answered',?)",
            [(aid, key, a["value"], now()) for key, a in answers.items()],
        )
        row = c.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone()
        actor = c.execute("SELECT id FROM accounts WHERE username='doctor'").fetchone()[
            0
        ]
        event = safety_alerts.record(c, path, row, {}, answers, 1, actor)
    return event


@contextmanager
def server(path, output):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, COMPASS_DB=str(path), PYTHONPATH=str(Path.cwd()))
    start = time.perf_counter()
    with (output / "benchmark-server.log").open("w") as log:
        process = subprocess.Popen(
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
            with httpx.Client(trust_env=False) as client:
                for _ in range(300):
                    try:
                        if client.get(base + "/", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Synthetic server exited; inspect benchmark-server.log"
                        )
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Synthetic server did not start")
            yield base, process, (time.perf_counter() - start) * 1000
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


async def load_level(base, count, users, rounds=8):
    samples, reads, writes, errors = [], [], [], []
    clients = [
        httpx.AsyncClient(
            base_url=base, trust_env=False, timeout=30, follow_redirects=False
        )
        for _ in range(count)
    ]
    tokens = {}
    auth_limit = asyncio.Semaphore(5)

    async def authenticate(index):
        pid = users[index]
        client = clients[index]
        async with auth_limit:
            token = csrf(await client.get("/login/patient"))
            logged = await client.post(
                "/login",
                data=dict(
                    username=f"metric-{pid}",
                    password=TEST_PASSWORD,
                    portal="patient",
                    csrf=token,
                ),
            )
            if logged.status_code != 303:
                raise RuntimeError(f"Load login failed: {logged.status_code}")
            tokens[index] = csrf(await client.get(f"/patients/{pid}/diary"))

    async def worker(index):
        pid, client, token = users[index], clients[index], tokens[index]
        for sequence in range(rounds):
            method = "POST" if sequence % 4 == 3 else "GET"
            start = time.perf_counter()
            try:
                if method == "POST":
                    response = await client.post(
                        f"/patients/{pid}/diary",
                        data=dict(
                            csrf=token,
                            day=today().isoformat(),
                            wellbeing=6,
                            sleep=7,
                            adherence="none",
                            note="SYNTHETIC LOAD MEASUREMENT",
                        ),
                    )
                else:
                    response = await client.get(f"/patients/{pid}/diary")
                if response.status_code != (303 if method == "POST" else 200):
                    errors.append(dict(method=method, status=response.status_code))
            except httpx.HTTPError as exc:
                errors.append(dict(method=method, error=type(exc).__name__))
            duration = (time.perf_counter() - start) * 1000
            samples.append(duration)
            (writes if method == "POST" else reads).append(duration)

    try:
        await asyncio.gather(*(authenticate(i) for i in range(count)))
        start = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(count)))
        elapsed = time.perf_counter() - start
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    return dict(
        concurrent_sessions=count,
        requests=len(samples),
        read=stats(reads),
        write=stats(writes),
        all=stats(samples),
        errors=errors,
        elapsed_s=round(elapsed, 3),
        throughput_rps=round(len(samples) / elapsed, 2),
    )


def crash_measurements(path):
    sentinel = "UNCOMMITTED SYNTHETIC CRASH ROW"
    with connect(path) as c:
        critical_before = c.execute(
            "SELECT count(*) FROM safety_urgent_snapshots"
        ).fetchone()[0]
    code = """import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);c.execute('BEGIN IMMEDIATE')
c.execute(\"INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(1,'2026-10-28',5,7,'none',?,0)\",(sys.argv[2],))
print('TRANSACTION OPEN',flush=True)
sys.stdin.read()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path), sentinel],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        if child.stdout.readline().strip() != "TRANSACTION OPEN":
            raise RuntimeError("Crash fixture failed to prepare a transaction")
        start = time.perf_counter()
        child.kill()
        child.wait(timeout=5)
        with connect(path) as c:
            result = dict(
                integrity=c.execute("PRAGMA integrity_check").fetchone()[0],
                uncommitted_rows=c.execute(
                    "SELECT count(*) FROM diary WHERE note=?", (sentinel,)
                ).fetchone()[0],
                committed_critical_snapshots_before=critical_before,
                committed_critical_snapshots_after=c.execute(
                    "SELECT count(*) FROM safety_urgent_snapshots"
                ).fetchone()[0],
            )
        result["kill_to_readable_ms"] = round((time.perf_counter() - start) * 1000, 3)
        return result
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def backup_measurements(path, root):
    result = {}
    before_event = add_urgent(path)
    target = root / "restored" / "copy.db"
    start = time.perf_counter()
    backup(path, target)
    result["snapshot_ms"] = round((time.perf_counter() - start) * 1000, 3)
    result["bytes"] = target.stat().st_size
    result["key_included"] = Path(str(target) + ".key").exists()
    with connect(target) as c:
        result["integrity"] = c.execute("PRAGMA integrity_check").fetchone()[0]
        result["critical_snapshot_readable"] = any(
            n["id"] == before_event for n in safety_alerts.pending(c, target, 1)
        )
    # A committed post-backup event is deliberately absent in the old snapshot.
    later = add_urgent(path)
    with connect(target) as c:
        result["post_backup_critical_event_in_old_snapshot"] = bool(
            c.execute("SELECT 1 FROM assessment_events WHERE id=?", (later,)).fetchone()
        )
    with server(target, root) as (base, process, startup_ms):
        result["restored_web_start_ms"] = round(startup_ms, 3)
        with httpx.Client(
            base_url=base, trust_env=False, follow_redirects=False
        ) as client:
            start = time.perf_counter()
            sign_in(client)
            response = client.get("/api/safety-messages")
            result["restored_auth_and_message_ms"] = round(
                (time.perf_counter() - start) * 1000, 3
            )
            result["restored_critical_messages"] = response.json()["count"]

    # Minimal new installation: a safety message is the ONLY encrypted content.
    minimal = root / "safety-only" / "source.db"
    minimal.parent.mkdir()
    with TestClient(create_app(minimal)) as client:
        provision(client)
    add_urgent(minimal)
    minimal_target = root / "safety-only-restored" / "copy.db"
    backup(minimal, minimal_target)
    minimal_result = dict(key_included=Path(str(minimal_target) + ".key").exists())
    try:
        with connect(minimal_target) as c:
            minimal_result["decrypted"] = bool(
                safety_alerts.pending(c, minimal_target, 1)
            )
    except Exception as exc:
        minimal_result.update(
            decrypted=False,
            error=type(exc).__name__,
            status=getattr(exc, "status_code", None),
        )
    result["safety_only_backup"] = minimal_result
    return result


def security_checks(base, path):
    checks = []
    with httpx.Client(base_url=base, trust_env=False, follow_redirects=False) as guest:
        for endpoint in [
            "/doctor/messages",
            "/api/safety-messages",
            "/patients/100/diary",
        ]:
            response = guest.get(endpoint)
            checks.append(
                dict(
                    check="anonymous_access",
                    path=endpoint,
                    status=response.status_code,
                    passed=response.status_code in [303, 401, 403],
                )
            )
    with httpx.Client(
        base_url=base, trust_env=False, follow_redirects=False
    ) as patient:
        sign_in(patient, "metric-100", "patient")
        for endpoint in [
            "/api/safety-messages",
            "/patients/101/diary",
            "/patients/100/ai-analysis",
        ]:
            response = patient.get(endpoint)
            checks.append(
                dict(
                    check="patient_role_and_ownership",
                    path=endpoint,
                    status=response.status_code,
                    passed=response.status_code in [403, 404],
                )
            )
        response = patient.post(
            "/patients/100/diary",
            data=dict(
                day=today().isoformat(),
                wellbeing=5,
                sleep=7,
                adherence="none",
                csrf="invalid-synthetic-token",
                note="MUST NOT BE SAVED",
            ),
        )
        checks.append(
            dict(
                check="csrf_invalid",
                status=response.status_code,
                passed=response.status_code == 403,
            )
        )
        token = csrf(patient.get("/patients/100/diary"))
        for value in [-1, 11, "invalid", ""]:
            response = patient.post(
                "/patients/100/diary",
                data=dict(
                    day=today().isoformat(),
                    wellbeing=value,
                    sleep=7,
                    adherence="none",
                    csrf=token,
                    note="MUST NOT BE SAVED",
                ),
            )
            checks.append(
                dict(
                    check="invalid_wellbeing_input",
                    value=value,
                    status=response.status_code,
                    passed=response.status_code == 422,
                )
            )
    with httpx.Client(base_url=base, trust_env=False, follow_redirects=False) as other:
        sign_in(other, "metric-other-doctor")
        response = other.get("/patients/100/ai-analysis")
        checks.append(
            dict(
                check="other_doctor_ownership",
                status=response.status_code,
                passed=response.status_code in [403, 404],
            )
        )
    with connect(path) as c:
        rejected_saved = c.execute(
            "SELECT count(*) FROM diary WHERE note='MUST NOT BE SAVED'"
        ).fetchone()[0]
    checks.append(
        dict(
            check="invalid_writes_leave_no_rows",
            rows=rejected_saved,
            passed=rejected_saved == 0,
        )
    )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    working = Path("data/compass.db")
    original_hash = (
        hashlib.sha256(working.read_bytes()).hexdigest() if working.exists() else None
    )
    result = dict(
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        at=now(),
        python=platform.python_version(),
        platform=platform.platform(),
        cpu_count=os.cpu_count(),
        cpu_affinity_count=len(os.sched_getaffinity(0)),
        sqlite=sqlite3.sqlite_version,
        packages={
            name: importlib.metadata.version(name)
            for name in ["fastapi", "uvicorn", "httpx", "cryptography"]
        },
        working_database_sha256_before=original_hash,
    )
    for filename in ("cpu.max", "memory.max"):
        p = Path("/sys/fs/cgroup") / filename
        result[filename] = p.read_text().strip() if p.exists() else None
    with tempfile.TemporaryDirectory(prefix="compass-technical-metrics-") as folder:
        root = Path(folder)
        path = root / "synthetic.db"
        with TestClient(create_app(path)) as client:
            provision(client)
        with connect(path) as c:
            seed(c, path)
            doctor = dict(
                c.execute("SELECT * FROM accounts WHERE username='doctor'").fetchone()
            )
            c.execute(
                "INSERT INTO accounts(username,password_hash,role,doctor_id) VALUES('metric-other-doctor',?,'doctor',2)",
                (TEST_HASH,),
            )
            users = list(range(100, 150))
            for pid in users:
                c.execute(
                    "INSERT INTO patients(id,name,doctor_id) VALUES(?,?,1)",
                    (pid, f"SYNTHETIC METRIC PATIENT {pid}"),
                )
                c.execute(
                    "INSERT INTO accounts(username,password_hash,role,patient_id) VALUES(?,?,'patient',?)",
                    (f"metric-{pid}", TEST_HASH, pid),
                )
                c.execute(
                    "INSERT INTO consents(patient_id,version,accepted,adult,created_at) VALUES(?,'SYNTHETIC-METRICS',1,1,?)",
                    (pid, now()),
                )
                c.executemany(
                    "INSERT INTO diary(patient_id,day,wellbeing,sleep,adherence,note,contact) VALUES(?,?,6,7,'none','SYNTHETIC BENCHMARK',0)",
                    [
                        (pid, (today() - timedelta(days=day)).isoformat())
                        for day in range(90)
                    ],
                )
            result["dataset"] = {
                table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("patients", "accounts", "diary", "assessments", "answers")
            }
            result["schema"] = dict(
                tables=c.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0],
                indexes=c.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='index'"
                ).fetchone()[0],
                migrations=c.execute(
                    "SELECT count(*) FROM schema_migrations"
                ).fetchone()[0],
                foreign_key_check=[
                    list(row) for row in c.execute("PRAGMA foreign_key_check")
                ],
                journal_mode=c.execute("PRAGMA journal_mode").fetchone()[0],
                synchronous=c.execute("PRAGMA synchronous").fetchone()[0],
                diary_query_plan=[
                    list(row)
                    for row in c.execute(
                        "EXPLAIN QUERY PLAN SELECT * FROM diary WHERE patient_id=100 ORDER BY day DESC LIMIT 90"
                    )
                ],
            )
        print("Synthetic dataset ready", flush=True)
        with connect(path) as c:
            pids = {
                r["scenario"]: r["patient_id"]
                for r in c.execute("SELECT * FROM aviandr_scenarios")
            }
        result["offline_analysis"] = {}
        for letter in "CD":
            with connect(path) as c:
                result["offline_analysis"][letter] = timed(
                    lambda: aviandr_analysis.build(c, path, doctor, pids[letter]), 30
                )
                report = aviandr_analysis.build(c, path, doctor, pids[letter])
                result["offline_analysis"][letter]["directions"] = len(
                    report["directions"]
                )
                result["offline_analysis"][letter]["basis_entries"] = sum(
                    len(d["narrative"]["basis"]) for d in report["directions"]
                )
                result["offline_analysis"][letter]["verified_official_sources"] = len(
                    report["sources"]
                )
        with connect(path) as c:
            result["sql_read"] = timed(
                lambda: c.execute(
                    "SELECT * FROM diary WHERE patient_id=? ORDER BY day DESC LIMIT 90",
                    (100,),
                ).fetchall(),
                200,
            )
        with server(path, output) as (base, process, startup_ms):
            result["startup_ms"] = round(startup_ms, 3)
            result["security_checks"] = security_checks(base, path)
            with httpx.Client(
                base_url=base, trust_env=False, follow_redirects=False, timeout=30
            ) as client:
                sign_in(client)
                endpoints = (
                    "/",
                    "/doctor",
                    "/doctor/messages",
                    "/api/safety-messages",
                    "/medicines",
                    f"/patients/{pids['C']}/ai-analysis",
                )
                result["http_read"] = {}
                for endpoint in endpoints:
                    response = client.get(endpoint)
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"Benchmark route unavailable {endpoint}: {response.status_code}"
                        )
                    result["http_read"][endpoint] = timed(
                        lambda: client.get(endpoint).raise_for_status(), 30
                    )
                response = client.get("/doctor")
                result["headers"] = dict(
                    csp=bool(response.headers.get("content-security-policy")),
                    nosniff=response.headers.get("x-content-type-options"),
                    cache_control=response.headers.get("cache-control"),
                    session_httponly="httponly"
                    in response.headers.get("set-cookie", "").lower(),
                    session_samesite=(
                        re.search(
                            r"samesite=([^; ]+)",
                            response.headers.get("set-cookie", "").lower(),
                        )[1]
                        if re.search(
                            r"samesite=([^; ]+)",
                            response.headers.get("set-cookie", "").lower(),
                        )
                        else None
                    ),
                    session_secure="secure"
                    in response.headers.get("set-cookie", "").lower(),
                )
                result["notice_api_after_commit"] = []
                for _ in range(10):
                    start = time.perf_counter()
                    eid = add_urgent(path)
                    saved = time.perf_counter()
                    response = client.get("/api/safety-messages")
                    response.raise_for_status()
                    result["notice_api_after_commit"].append(
                        dict(
                            commit_ms=(saved - start) * 1000,
                            api_after_commit_ms=(time.perf_counter() - saved) * 1000,
                            found=f'data-urgent-id="{eid}"'
                            in response.json()["messages_html"],
                        )
                    )
            result["load"] = []
            for concurrency in [1, 5, 10, 25, 50]:
                value = asyncio.run(load_level(base, concurrency, users))
                result["load"].append(value)
                print(
                    f"Load {concurrency}: {value['requests']} requests, {len(value['errors'])} errors, p95={value['all']['p95_ms']} ms",
                    flush=True,
                )
            try:
                import psutil

                result["server_rss_bytes"] = (
                    psutil.Process(process.pid).memory_info().rss
                )
            except ImportError:
                status = Path(f"/proc/{process.pid}/status").read_text()
                result["server_rss_bytes"] = (
                    int(re.search(r"VmRSS:\s+(\d+)", status)[1]) * 1024
                )
        result["backup"] = backup_measurements(path, root)
        result["crash"] = crash_measurements(path)
        with httpx.Client(trust_env=False, timeout=2) as client:
            try:
                response = client.get("http://127.0.0.1:11434/api/tags")
                result["ollama"] = dict(
                    status=response.status_code,
                    models=[m.get("name") for m in response.json().get("models", [])],
                )
            except httpx.HTTPError as exc:
                result["ollama"] = dict(available=False, error=type(exc).__name__)
        result["synthetic_database_bytes"] = path.stat().st_size
    result["working_database_sha256_after"] = (
        hashlib.sha256(working.read_bytes()).hexdigest() if working.exists() else None
    )
    result["working_database_unchanged"] = (
        result["working_database_sha256_after"] == original_hash
    )
    (output / "benchmarks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print("Saved", output / "benchmarks.json", flush=True)


if __name__ == "__main__":
    main()
