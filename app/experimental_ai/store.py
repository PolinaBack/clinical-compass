"""Dedicated SQLite store. Opening a clinical DB or a modified fixture fails closed."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from .scenarios import SCENARIO, canonical, digest, trusted

ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "experiment_manifest",
    "experiment_patient",
    "experiment_observations",
    "experiment_questionnaires",
    "experiment_runs",
    "experiment_reviews",
}


class ExperimentError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_path():
    return Path(
        os.getenv("COMPASS_EXPERIMENT_DB", str(ROOT / "data/experimental_ai/demo.db"))
    )


def protected_path(path, main_path=None):
    path = Path(path).resolve()
    mains = [
        ROOT / "data/compass.db",
        Path(os.getenv("COMPASS_DB", str(ROOT / "data/compass.db"))),
    ]
    if main_path is not None:
        mains.append(Path(main_path))
    for main in mains:
        if path == main.resolve() or (
            path.exists() and main.exists() and path.samefile(main)
        ):
            raise ExperimentError("clinical_database_forbidden")
    return path


@contextmanager
def connect(path=None, *, main_path=None):
    path = protected_path(path or default_path(), main_path)
    if not path.is_file():
        raise ExperimentError("demo_not_initialized")
    c = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        with c:
            yield c
    except sqlite3.DatabaseError:
        raise ExperimentError("invalid_demo_database") from None
    finally:
        c.close()


def load(c):
    try:
        schema = c.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','view','trigger')"
        ).fetchall()
        if {(r["name"], r["type"]) for r in schema} != {(t, "table") for t in TABLES}:
            raise ExperimentError("synthetic_integrity_failed")
        manifest = c.execute("SELECT * FROM experiment_manifest").fetchall()
        expected = trusted()
        if (
            len(manifest) != 1
            or manifest[0]["scenario_id"] != SCENARIO
            or manifest[0]["dataset_hash"] != digest(expected)
        ):
            raise ExperimentError("synthetic_integrity_failed")
        patients = c.execute("SELECT payload FROM experiment_patient").fetchall()
        if len(patients) != 1:
            raise ExperimentError("synthetic_integrity_failed")
        result = dict(
            scenario_id=SCENARIO,
            provenance=expected["provenance"],
            patient=json.loads(patients[0][0]),
            observations=[
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM experiment_observations ORDER BY day"
                )
            ],
            questionnaires=[
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM experiment_questionnaires ORDER BY ordinal"
                )
            ],
        )
        if canonical(result) != canonical(expected):
            raise ExperimentError("synthetic_integrity_failed")
        return result
    except (sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        raise ExperimentError("synthetic_integrity_failed") from None


def initialize(path=None, *, main_path=None):
    path = protected_path(path or default_path(), main_path)
    if path.exists():
        with connect(path, main_path=main_path) as c:
            load(c)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never truncates or overwrites any existing file.
    with path.open("xb"):
        pass
    c = sqlite3.connect(path)
    try:
        with c:
            c.executescript(
                """
            CREATE TABLE experiment_manifest(scenario_id TEXT PRIMARY KEY,dataset_hash TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE experiment_patient(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
            CREATE TABLE experiment_observations(day TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE experiment_questionnaires(ordinal INTEGER PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE experiment_runs(id INTEGER PRIMARY KEY,scenario_id TEXT NOT NULL,dataset_hash TEXT NOT NULL,actor_id INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('queued','running','completed','error')),stage TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,heartbeat_at TEXT,finished_at TEXT,error_code TEXT,result TEXT,metrics TEXT,provenance TEXT);
            CREATE UNIQUE INDEX one_active_experiment ON experiment_runs(scenario_id) WHERE status IN ('queued','running');
            CREATE TABLE experiment_reviews(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES experiment_runs(id),actor_id INTEGER NOT NULL,grounding INTEGER NOT NULL CHECK(grounding BETWEEN 0 AND 2),usefulness INTEGER NOT NULL CHECK(usefulness BETWEEN 0 AND 2),safety INTEGER NOT NULL CHECK(safety BETWEEN 0 AND 2),issue TEXT NOT NULL,created_at TEXT NOT NULL);
            """
            )
            data = trusted()
            c.execute(
                "INSERT INTO experiment_manifest VALUES(?,?,?)",
                (SCENARIO, digest(data), now()),
            )
            c.execute(
                "INSERT INTO experiment_patient VALUES(1,?)",
                (canonical(data["patient"]),),
            )
            c.executemany(
                "INSERT INTO experiment_observations VALUES(?,?)",
                [(r["day"], canonical(r)) for r in data["observations"]],
            )
            c.executemany(
                "INSERT INTO experiment_questionnaires VALUES(?,?)",
                [(i, canonical(r)) for i, r in enumerate(data["questionnaires"])],
            )
    finally:
        c.close()
    with connect(path, main_path=main_path) as c:
        load(c)
    return True
