"""Readable presentation only; original model responses stay unchanged in storage."""

import json
from markdown_it import MarkdownIt
from markupsafe import Markup, escape


def _image_text(tokens, index, options, env):
    # Keep the model's caption without loading remote resources in the chart.
    return str(escape(tokens[index].content))


_markdown = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_markdown.renderer.rules["image"] = _image_text
_markdown.renderer.rules["table_open"] = lambda *args: (
    '<div class="ai-markdown-table" tabindex="0" role="region" '
    'aria-label="Таблица из ответа ИИ"><table>\n'
)
_markdown.renderer.rules["table_close"] = lambda *args: "</table></div>\n"

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


def formatted_text(text):
    """Render model Markdown with escaped HTML; never modify the saved response."""
    readable = readable_text(text)
    try:
        value = json.loads(readable)
    except (TypeError, ValueError, RecursionError):
        value = None
    if isinstance(value, (dict, list)):
        return Markup("<pre><code>") + escape(readable) + Markup("</code></pre>")
    return Markup(_markdown.render(str(readable or "")))
