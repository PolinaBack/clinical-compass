"""Local return destinations for failed HTML actions, without external redirects."""

from urllib.parse import unquote, urlsplit


def local_destination(request, value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme.lower(), parsed.netloc.lower()) != (
                request.url.scheme.lower(),
                request.url.netloc.lower(),
            ):
                return None
        decoded = unquote(value)
        path = unquote(parsed.path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "\\" in decoded
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
        ):
            return None
        if path.startswith(("/api/", "/static/")) or path == "/logout":
            return None
        return (
            parsed.path
            + ("?" + parsed.query if parsed.query else "")
            + ("#" + parsed.fragment if parsed.fragment else "")
        )
    except (ValueError, UnicodeError):
        return None


async def error_destination(request):
    supplied = None
    if request.method == "POST" and request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        try:
            supplied = (await request.form()).get("return_to")
        except (RuntimeError, ValueError):
            pass
    for candidate in (
        supplied,
        request.headers.get("referer"),
        request.session.get("last_html_page"),
    ):
        destination = local_destination(request, candidate)
        if destination and (
            request.method != "GET"
            or destination.split("#")[0]
            != str(request.url).removeprefix(str(request.base_url).rstrip("/"))
        ):
            return destination
    return None
