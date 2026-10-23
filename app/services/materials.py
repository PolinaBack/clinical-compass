"""Named external resources and durable clinician assignments, separate from drugs."""

import ipaddress
from urllib.parse import urlsplit, urlunsplit
from fastapi import HTTPException
from app.storage import now, audit
from app.services.intake import check_scope
from app.services.clinical_forms import today

SEEDS = [
    (
        "Что делать, если началась паническая атака?",
        "https://www.mind.org.uk/information-support/types-of-mental-health-problems/panic-attacks/",
        "Mind",
    ),
    (
        "Что делать, когда мысли не дают покоя?",
        "https://www.mind.org.uk/information-support/types-of-mental-health-problems/anxiety-problems/how-to-manage-anxiety-and-worry/",
        "Mind",
    ),
    (
        "Упражнения на заземление",
        "https://www.nhsinform.scot/healthy-living/mental-wellbeing/breathing-and-relaxation-exercises/",
        "NHS inform",
    ),
    (
        "Дыхание при стрессе",
        "https://www.nhs.uk/mental-health/self-help/guides-tools-and-activities/breathing-exercises-for-stress/",
        "NHS",
    ),
    (
        "Почему при депрессии важно постепенно возвращаться к действиям, связанным с удовольствием и чувством достижения",
        "https://www.cci.health.wa.gov.au/~/media/CCI/Mental-Health-Professionals/Depression/Depression---Information-Sheets/Depression-Information-Sheet--05--Behavioural-Activation-Fun--Achievement.pdf",
        "Centre for Clinical Interventions · PDF",
    ),
    ("Мысли по кругу", "https://cuprum.media/mentalnoe/rumination", "Купрум"),
    ("Куда пропал интерес?", "https://cuprum.media/mentalnoe/anhedonia", "Купрум"),
    (
        "Достаточно хорошо — уже хорошо",
        "https://mental-health-russia.ru/idealov-ne-suschestvuet-kak-perfektsionizm-meshaet-nam-zhit/",
        "Союз охраны психического здоровья",
    ),
    (
        "Что в моей власти?",
        "https://slozhno.live/article/kak-zhit-i-rabotat-kogda-vokrug--neopredelennost/",
        "Сложно",
    ),
    (
        "Мои достижения без обесценивания",
        "https://slozhno.live/article/sindrom-samozvanca/",
        "Сложно",
    ),
    ("Куда уходит моя энергия?", "https://slozhno.live/article/vygoranie/", "Сложно"),
    ("Что мне сейчас нужно?", "https://slozhno.live/article/regressiya/", "Сложно"),
]
STATUS = {
    "assigned": "Назначено",
    "opened": "Открыто",
    "read": "Прочитано",
    "later": "Вернуться позже",
}


def bootstrap(c):
    for index, (title, url, publisher) in enumerate(SEEDS, 1):
        c.execute(
            "INSERT OR IGNORE INTO resource_items(seed_key,title,url,publisher,created_at) VALUES(?,?,?,?,?)",
            (f"user-2026-10-23-{index}", title, url, publisher, now()),
        )


def valid_url(raw):
    if (
        not isinstance(raw, str)
        or not 1 <= len(raw) <= 2000
        or any(ord(x) < 33 for x in raw)
    ):
        raise HTTPException(422, "Укажите ссылку HTTPS без пробелов")
    try:
        u = urlsplit(raw)
        host = u.hostname or ""
        if (
            u.scheme != "https"
            or u.username
            or u.password
            or u.port not in (None, 443)
            or "." not in host
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError()
        except ValueError as error:
            if ":" in host or all(ch in "0123456789." for ch in host):
                raise ValueError() from error
        host.encode("idna")
    except (ValueError, UnicodeError):
        raise HTTPException(
            422, "Нужна HTTPS-ссылка на внешний сайт без пароля и локального адреса"
        ) from None
    return urlunsplit(u)


def doctor(c, user):
    active = c.execute(
        "SELECT * FROM accounts WHERE id=? AND active=1", (user["id"],)
    ).fetchone()
    if (
        not active
        or active["role"] != "doctor"
        or active["auth_version"] != user["auth_version"]
    ):
        raise HTTPException(403, "Раздел доступен специалисту")


def items(c, user):
    doctor(c, user)
    return [
        dict(r)
        for r in c.execute(
            "SELECT * FROM resource_items WHERE created_by IS NULL OR created_by=? ORDER BY id",
            (user["id"],),
        )
    ]


def create(c, user, form):
    doctor(c, user)
    title = str(form.get("title", "")).strip()
    description = str(form.get("description", "")).strip()
    if not 1 <= len(title) <= 250 or len(description) > 1500:
        raise HTTPException(422, "Название — до 250 символов, описание — до 1500")
    url = valid_url(str(form.get("url", "")).strip())
    existing = c.execute(
        "SELECT id FROM resource_items WHERE url=? AND (created_by IS NULL OR created_by=?)",
        (url, user["id"]),
    ).fetchone()
    if existing:
        return existing["id"]
    ident = c.execute(
        "INSERT INTO resource_items(title,url,description,created_by,created_at) VALUES(?,?,?,?,?)",
        (title, url, description, user["id"], now()),
    ).lastrowid
    audit(c, user["id"], "resource_created", None, ident)
    return ident


def assign(c, user, rid, pid, form):
    doctor(c, user)
    check_scope(c, user, pid, doctor=True)
    item = c.execute(
        "SELECT * FROM resource_items WHERE id=? AND (created_by IS NULL OR created_by=?)",
        (rid, user["id"]),
    ).fetchone()
    if not item:
        raise HTTPException(404, "Ресурс не найден")
    note = str(form.get("note", "")).strip()
    due = str(form.get("due_day", "")).strip()
    if len(note) > 1000:
        raise HTTPException(422, "Комментарий — до 1000 символов")
    if due:
        from datetime import date

        try:
            if date.fromisoformat(due) < today():
                raise ValueError()
        except ValueError:
            raise HTTPException(
                422, "Срок чтения должен быть сегодня или позже"
            ) from None
    old = c.execute(
        "SELECT id FROM resource_assignments WHERE resource_id=? AND patient_id=?",
        (rid, pid),
    ).fetchone()
    if old:
        return old["id"]
    aid = c.execute(
        "INSERT INTO resource_assignments(resource_id,patient_id,assigned_by,title,url,note,due_day,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (rid, pid, user["id"], item["title"], item["url"], note, due, now(), now()),
    ).lastrowid
    event(c, user, aid, "assigned")
    audit(c, user["id"], "resource_assigned", pid, aid)
    return aid


def event(c, user, aid, kind):
    c.execute(
        "INSERT INTO resource_events(assignment_id,actor_id,kind,created_at) VALUES(?,?,?,?)",
        (aid, user["id"], kind, now()),
    )


def assignment(c, user, aid, write=False):
    row = c.execute("SELECT * FROM resource_assignments WHERE id=?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404, "Назначение не найдено")
    check_scope(c, user, row["patient_id"], write=write)
    return dict(row)
