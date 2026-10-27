"""Remove literal repetition without guessing which clinical facts are equivalent."""

from copy import deepcopy
import re
import unicodedata


def _key(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(
        ".!;"
    )


def unique_summary(data, *, included_only=False):
    result = deepcopy(data)
    seen_items, seen_sentences = set(), set()
    # Keep an explicit action in its actionable section if it is also repeated
    # verbatim in the narrative. Private suggestions must never hide public text.
    for kind in ("next_steps", "summary", "clarifications"):
        kept = []
        for item in result.get(kind, []):
            if not item.get("included", True):
                if not included_only:
                    kept.append(item)
                continue
            original = item["text"]
            key = _key(original)
            if not key or key in seen_items:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", original.strip())
            context = (item.get("quote"), tuple(item.get("segment_ids", [])))
            unique = []
            for sentence in sentences:
                sentence_key = _key(sentence)
                # Avoid treating abbreviations or short contextual replies as
                # standalone duplicate sentences inside a longer paragraph.
                substantial = len(sentence_key) >= 12 and len(sentence_key.split()) >= 2
                if substantial and (context, sentence_key) in seen_sentences:
                    continue
                unique.append(sentence)
                if substantial:
                    seen_sentences.add((context, sentence_key))
            seen_items.add(key)
            if unique:
                item["text"] = " ".join(unique)
                kept.append(item)
        result[kind] = kept
    return result
