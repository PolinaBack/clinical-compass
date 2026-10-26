"""Read public HTTPS medicine pages without executing scripts or trusting claims."""

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException

MAX_HTML = 2 * 1024 * 1024
SECTIONS = {
    "group": "Группа",
    "indications": "Показания",
    "doses": "Способ применения и дозы",
    "composition": "Состав",
    "contraindications": "Противопоказания",
    "warnings": "Особые указания",
    "adverse": "Побочные действия",
    "interactions": "Взаимодействия с другими лекарствами",
    "pharmacodynamics": "Фармакодинамика",
    "pharmacokinetics": "Фармакокинетика",
    "storage": "Условия хранения",
    "shelf_life": "Срок годности",
    "pharmacovigilance": "Фармаконадзор",
}
ALIASES = {
    "group": (
        "фармакотерапевтическая группа",
        "фармакологическая группа",
        "клинико фармакологическая группа",
    ),
    "indications": ("показания", "показания к применению", "показания препарата"),
    "doses": ("способ применения и дозы", "режим дозирования", "дозировка"),
    "composition": ("состав", "форма выпуска состав и упаковка"),
    "contraindications": ("противопоказания", "противопоказания к применению"),
    "warnings": ("особые указания", "меры предосторожности"),
    "adverse": (
        "побочные действия",
        "побочное действие",
        "нежелательные реакции",
        "побочные эффекты",
    ),
    "interactions": (
        "лекарственное взаимодействие",
        "взаимодействие",
        "взаимодействие с другими лекарственными средствами",
    ),
    "pharmacodynamics": ("фармакодинамика", "фармакологическое действие"),
    "pharmacokinetics": ("фармакокинетика",),
    "storage": ("условия хранения", "условия хранения препарата"),
    "shelf_life": ("срок годности", "срок годности препарата"),
    "pharmacovigilance": ("фармаконадзор",),
}


def fail(message):
    raise HTTPException(422, message)


def normal(value):
    return re.sub(r"[^а-яa-z0-9]+", " ", value.casefold().replace("ё", "е")).strip()


def checked_url(value):
    try:
        p = urlsplit(value.strip())
        if (
            len(value) > 2000
            or p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 443)
        ):
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").rstrip(".")
        if not host or len(host) > 253:
            raise ValueError()
        if host.casefold() in {"localhost", "localhost.localdomain"} or host.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise ValueError()
        return p, host
    except (ValueError, UnicodeError):
        fail("Укажите публичную HTTPS-ссылку без логина, пароля и нестандартного порта")


class PublicTransport(httpx.BaseTransport):
    """Resolve once and connect to a checked IP, keeping TLS SNI and Host."""

    def __init__(self, resolver=None, inner=None):
        self.resolver = resolver or socket.getaddrinfo
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        _, host = checked_url(str(request.url))
        try:
            addresses = {
                entry[4][0]
                for entry in self.resolver(host, 443, type=socket.SOCK_STREAM)
            }
            if not addresses or any(
                not ipaddress.ip_address(a).is_global
                or ipaddress.ip_address(a).is_multicast
                for a in addresses
            ):
                fail("Локальные и внутренние сетевые адреса для загрузки не разрешены")
            address = sorted(
                addresses, key=lambda a: (ipaddress.ip_address(a).version, a)
            )[0]
        except HTTPException:
            raise
        except (OSError, ValueError):
            fail(
                "Не удалось определить публичный адрес сайта. Можно заполнить карточку вручную"
            )
        headers = dict(request.headers)
        headers["host"] = "[" + host + "]" if ":" in host else host
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return self.inner.handle_request(pinned)

    def close(self):
        self.inner.close()


def fetch(url, *, transport=None):
    checked_url(url)
    try:
        with httpx.Client(
            transport=transport or PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(12, connect=5),
        ) as client:
            for hop in range(4):
                checked_url(url)
                with client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": "ClinicalCompass/1.0 (reference preview)",
                    },
                ) as response:
                    if response.is_redirect:
                        if hop == 3 or not response.headers.get("location"):
                            fail("Слишком много перенаправлений сайта")
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        fail(
                            f"Сайт не отдал страницу (HTTP {response.status_code}). Заполните карточку вручную или используйте другую ссылку"
                        )
                    if response.headers.get("content-type", "").split(";", 1)[
                        0
                    ].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                        fail(
                            "Нужна HTML-страница препарата. Для PDF или другого файла заполните карточку вручную"
                        )
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_HTML:
                            fail(
                                "Страница превышает 2 МБ; используйте другую ссылку или ручное заполнение"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    declared = re.search(
                        rb'charset\s*=\s*["\']?([\w-]+)', content[:4096], re.I
                    )
                    if (
                        "charset="
                        not in response.headers.get("content-type", "").lower()
                        and declared
                    ):
                        encoding = declared[1].decode("ascii", errors="ignore")
                    if encoding.lower() not in {
                        "utf-8",
                        "utf8",
                        "windows-1251",
                        "cp1251",
                        "koi8-r",
                        "iso-8859-1",
                    }:
                        encoding = "utf-8"
                    return content.decode(encoding, errors="replace"), url
    except HTTPException:
        raise
    except (httpx.HTTPError, OSError, ValueError):
        fail(
            "Не удалось загрузить страницу. Проверьте ссылку или заполните карточку вручную"
        )


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.heading = None
        self.heading_text = []
        self.active = None
        self.title = False
        self.titles = []
        self.h1 = []
        self.meta = {}
        self.sections = {}
        self.text = []
        self.json_script = False
        self.script = []
        self.json_blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "nav", "footer", "noscript"}:
            if (
                tag == "script"
                and attrs.get("type", "").lower() == "application/ld+json"
            ):
                self.json_script = True
                self.script = []
            self.skip += 1
        if self.skip:
            return
        if tag == "meta":
            self.meta[attrs.get("property", attrs.get("name", ""))] = attrs.get(
                "content", ""
            )
        if tag == "title":
            self.title = True
        if tag in {"h1", "h2", "h3", "h4"}:
            self.text.append("\n")
            self.heading = tag
            self.heading_text = []
            self.active = None
        if tag in {"p", "br", "li", "dt", "dd", "tr", "div"}:
            self.text.append("\n")
            if self.active:
                self.sections[self.active].append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "noscript"}:
            if tag == "script" and self.json_script:
                self.json_blocks.append("".join(self.script))
                self.json_script = False
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {"p", "div", "li", "dt", "dd", "tr"}:
            self.text.append("\n")
            if self.active:
                self.sections[self.active].append("\n")
        if tag == "title":
            self.title = False
        if tag == self.heading:
            text = " ".join("".join(self.heading_text).split())
            if tag == "h1":
                self.h1.append(text)
            normalized = normal(text)
            for key, aliases in ALIASES.items():
                if any(
                    normalized == alias or normalized.startswith(alias + " ")
                    for alias in aliases
                ):
                    self.active = key
                    self.sections.setdefault(key, [])
                    break
            self.heading = None

    def handle_data(self, value):
        if self.json_script:
            self.script.append(value)
        if self.skip:
            return
        if self.heading:
            self.heading_text.append(value)
        elif self.active:
            self.sections[self.active].append(value)
        if self.title:
            self.titles.append(value)
        self.text.append(value)


def parse(html):
    page = Page()
    try:
        page.feed(html)
    except (ValueError, RecursionError):
        fail("Не удалось прочитать структуру страницы; заполните поля вручную")
    drugs = []

    def visit(value, depth=0):
        if depth > 20:
            return
        if isinstance(value, list):
            for item in value[:100]:
                visit(item, depth + 1)
        if isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "Drug" or isinstance(kind, list) and "Drug" in kind:
                drugs.append(value)
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item, depth + 1)

    for block in page.json_blocks[:20]:
        try:
            visit(json.loads(block))
        except (ValueError, RecursionError):
            pass
    data = drugs[0] if len(drugs) == 1 else {}

    def string(value):
        if isinstance(value, dict):
            return string(value.get("name", ""))
        if isinstance(value, list):
            return ", ".join(filter(None, (string(v) for v in value[:20])))
        return str(value).strip() if isinstance(value, (str, int, float)) else ""

    result = dict(
        name=string(data.get("name"))
        or (page.h1[0] if page.h1 else page.meta.get("og:title", "")),
        inn=string(data.get("nonProprietaryName") or data.get("activeIngredient")),
        form=string(data.get("dosageForm")),
        manufacturer=string(data.get("manufacturer")),
    )
    strength = data.get("availableStrength")
    if isinstance(strength, list) and len(strength) == 1:
        strength = strength[0]
    if (
        isinstance(strength, dict)
        and strength.get("strengthValue") is not None
        and strength.get("strengthUnit")
    ):
        result["strength"] = (
            str(strength["strengthValue"]) + " " + string(strength["strengthUnit"])
        )
    elif isinstance(strength, str):
        result["strength"] = strength
    plain = re.sub(r"[ \t]+", " ", "".join(page.text))
    for key, labels in {
        "inn": r"МНН|Международное непатентованное наименование",
        "atc": r"Код АТХ|АТХ",
        "registration": r"Регистрационный номер",
        "manufacturer": r"Производитель",
    }.items():
        if result.get(key):
            continue
        match = re.search(r"(?:" + labels + r")\s*:?\s*\n?\s*([^\n]+)", plain, re.I)
        if match:
            result[key] = match[1].strip()
    for key, parts in page.sections.items():
        result["section_" + key] = re.sub(r"\n\s*\n+", "\n", "".join(parts)).strip()
    if result.get("section_group"):
        result["group"] = result["section_group"]
    return result, plain.strip()
