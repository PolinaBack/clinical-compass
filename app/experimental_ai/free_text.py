"""A model transcript is untrusted display data, never a clinical calculation."""

from .store import ExperimentError

MODES = {"free", "structured"}


def unverified_text(envelope):
    message = envelope.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ExperimentError("empty_response")
    # Keep the actual answer verbatim, including refusals and incomplete output.
    # Transport limits and HTML escaping are separate from content validation.
    return dict(
        output_format="free_text",
        text=content,
        incomplete=envelope.get("done") is not True
        or envelope.get("done_reason") == "length",
    )
