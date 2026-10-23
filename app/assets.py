"""Content-versioned public assets, including updates within a running app."""

from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

STATIC_ROOT = Path(__file__).resolve().parent / "static"


def asset_url(name: str) -> str:
    # Templates supply fixed filenames; never hash a file outside static/.
    path = (STATIC_ROOT / name).resolve()
    if path.parent != STATIC_ROOT.resolve() or path.suffix not in (".css", ".js"):
        raise ValueError("Expected a CSS or JavaScript file in static/")
    version = sha256(path.read_bytes()).hexdigest()[:16]
    return f"/static/{quote(name, safe='')}?v={version}"
