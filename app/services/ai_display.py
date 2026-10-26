"""Readable presentation only; original model responses stay unchanged in storage."""

import json

LABELS = {
    "summary": "",
    "hypotheses": "Гипотезы для проверки",
    "explanations": "Объяснение",
    "questions": "Что уточнить на консультации",
    "contradictions": "Противоречия и пробелы",
}
TECHNICAL = {"summary_evidence_ids", "material_ids"}


def readable_text(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return text
    if isinstance(value, str):
        return value if value.strip() else text
    try:
        pretty = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError, RecursionError):
        return text
    if not isinstance(value, dict) or set(value) - set(LABELS) - TECHNICAL:
        return pretty
    result = []
    for key, title in LABELS.items():
        items = value.get(key, [])
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            return pretty
        parts = []
        for item in items:
            if isinstance(item, dict):
                if set(item) - {"text", "direction", "gap_id", "evidence_ids"}:
                    return pretty
                item = item.get("text")
            if not isinstance(item, str):
                return pretty
            parts.append(item)
        if parts:
            result.append((title + "\n" if title else "") + "\n".join(parts))
    readable = "\n\n".join(result)
    return readable if readable.strip() else pretty
