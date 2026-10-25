"""Stable preference codes; excluded terms never become ordinary choices."""

import json
from pathlib import Path

CATALOG = json.loads(
    (Path(__file__).parent / "references" / "tolerability_dictionary.json").read_text(
        encoding="utf-8"
    )
)
OPTIONS = CATALOG["options"]
ELIGIBLE = {o["code"]: o for o in OPTIONS if o["allowed"]}
LABELS = {code: value["label"] for code, value in ELIGIBLE.items()}
SYSTEMS = sorted({o["system"] for o in ELIGIBLE.values()})
