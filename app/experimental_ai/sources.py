"""Small allowlisted corpus; candidates are never advertised as verified sources."""

import hashlib
import json
import os
from pathlib import Path

from .store import ROOT, ExperimentError, now

CATALOG = {
    "nice-depression": dict(
        title="Depression in adults: treatment and management",
        publisher="NICE",
        version="NG222",
        url="https://www.nice.org.uk/guidance/ng222",
        topics=["настроение", "подходы к помощи взрослым"],
        marker="Depression in adults",
    ),
    "nice-anxiety": dict(
        title="Generalised anxiety disorder and panic disorder in adults: management",
        publisher="NICE",
        version="CG113",
        url="https://www.nice.org.uk/guidance/cg113",
        topics=["тревога", "подходы к помощи взрослым"],
        marker="Generalised anxiety disorder",
    ),
    "nhs-cbt": dict(
        title="Cognitive behavioural therapy (CBT)",
        publisher="NHS",
        version="Страница NHS; версия — SHA-256 полученного документа",
        url="https://www.nhs.uk/mental-health/talking-therapies-medicine-treatments/talking-therapies-and-counselling/cognitive-behavioural-therapy-cbt/",
        topics=["психотерапия", "когнитивно-поведенческая терапия"],
        marker="Cognitive behavioural therapy",
    ),
}


def folder():
    return Path(
        os.getenv(
            "COMPASS_EXPERIMENT_SOURCES", str(ROOT / "data/experimental_ai/sources")
        )
    )


def catalog():
    items = []
    for key, source in CATALOG.items():
        item = dict(id=key, **source, verified=False, checked_at=None)
        try:
            meta = json.loads((folder() / (key + ".json")).read_text())
            content = (folder() / (key + ".html")).read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            if (
                0 < len(content) <= 3_000_000
                and meta["url"] == source["url"]
                and meta["sha256"] == checksum
                and meta["approved_sha256"] == checksum
                and source["marker"].casefold()
                in content.decode("utf-8", errors="replace").casefold()
                and meta["reviewer"].strip()
                and meta["reviewed_at"]
            ):
                item.update(
                    verified=True,
                    checked_at=meta["reviewed_at"],
                    fetched_at=meta["fetched_at"],
                    sha256=checksum,
                )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        items.append(item)
    return items


def retrieve(analysis):
    # A real, reproducible catalogue search by observed domains, not an open web search.
    topics = set()
    if analysis["metrics"]["wellbeing"]["n"]:
        topics.add("настроение")
    if analysis["metrics"]["anxiety"]["n"]:
        topics.add("тревога")
    topics.add("психотерапия")
    return [s for s in catalog() if s["verified"] and topics.intersection(s["topics"])]


def fetch(source_id, client):
    source = CATALOG[source_id]
    try:
        with client.stream("GET", source["url"]) as r:
            if r.status_code != 200 or "text/html" not in r.headers.get(
                "content-type", ""
            ):
                raise ExperimentError("source_unavailable")
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > 3_000_000:
                    raise ExperimentError("source_unavailable")
                chunks.append(chunk)
    except Exception as exc:
        if isinstance(exc, ExperimentError):
            raise
        raise ExperimentError("source_unavailable") from None
    content = b"".join(chunks)
    if (
        source["marker"].casefold()
        not in content.decode("utf-8", errors="replace").casefold()
    ):
        raise ExperimentError("source_unavailable")
    # Approval is invalidated on every download, even if the site looks similar.
    target = folder()
    target.mkdir(parents=True, exist_ok=True)
    (target / (source_id + ".html")).write_bytes(content)
    metadata = dict(
        url=source["url"],
        fetched_at=now(),
        sha256=hashlib.sha256(content).hexdigest(),
        approved_sha256=None,
    )
    (target / (source_id + ".json")).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    )
    return metadata


def approve(source_id, checksum, reviewer):
    target = folder()
    try:
        meta = json.loads((target / (source_id + ".json")).read_text())
        content = (target / (source_id + ".html")).read_bytes()
        source = CATALOG[source_id]
        if (
            not reviewer.strip()
            or meta["url"] != source["url"]
            or checksum != meta["sha256"]
            or hashlib.sha256(content).hexdigest() != checksum
        ):
            raise ValueError()
    except (OSError, ValueError, KeyError):
        raise ExperimentError("source_review_required") from None
    meta.update(
        approved_sha256=checksum, reviewer=reviewer.strip()[:80], reviewed_at=now()
    )
    (target / (source_id + ".json")).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2)
    )
