"""Patient cards have native fallback targets and keep the durable diary data."""

from html.parser import HTMLParser
import json

from app.services.clinical_forms import today
from app.storage import connect
from tests.test_diary_library_offline import store, call


class DiaryLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links, self.ids = [], set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and "diary-card" in attrs.get("class", "").split():
            self.links.append(attrs)


def test_core_diary_cards_keep_native_targets_even_with_old_hidden_preferences(store):
    _, path, users = store
    with connect(path) as c:
        c.execute(
            "INSERT INTO interface_preferences(account_id,minimal,cards_json,revision,updated_at) VALUES(?,1,?,1,'test')",
            (
                users["p1"]["id"],
                json.dumps(
                    [
                        dict(key=key, visible=False, pinned=False, position=i)
                        for i, key in enumerate(("wellbeing", "sleep", "treatment"), 1)
                    ]
                ),
            ),
        )
    page = call(store, "/patients/{pid}/diary", who="p1", pid=1)
    assert page.status_code == 200
    parsed = DiaryLinks(page.body.decode())
    assert [link["href"] for link in parsed.links] == [
        "#wellbeing-dialog",
        "#sleep-dialog",
        "#treatment-dialog",
    ]
    assert all(link["href"][1:] in parsed.ids for link in parsed.links)
    assert all(link["aria-controls"] == link["href"][1:] for link in parsed.links)
    assert all(
        title in page.body.decode()
        for title in ("Настроение и самочувствие", "Сон и восстановление", "Лечение")
    )


def test_restored_cards_keep_saved_zero_values_and_personal_metrics(store):
    _, path, _ = store
    assigned = call(
        store,
        "/patients/{pid}/diary/metrics",
        "POST",
        pid=1,
        form=dict(csrf="direct-unit-csrf", title="Энергия", kind="scale", unit=""),
    )
    assert assigned.status_code == 303
    with connect(path) as c:
        mid = c.execute("SELECT id FROM diary_metrics WHERE patient_id=1").fetchone()[0]
    saved = call(
        store,
        "/patients/{pid}/diary",
        "POST",
        who="p1",
        pid=1,
        csrf="direct-unit-csrf",
        day=today(),
        wellbeing=0,
        sleep=0.0,
        sleep_quality="0",
        adherence="none",
        note="Сохранённая тестовая запись",
        contact=False,
        form=dict(
            csrf="direct-unit-csrf",
            day=today().isoformat(),
            wellbeing="0",
            sleep="0",
            sleep_quality="0",
            adherence="none",
            note="Сохранённая тестовая запись",
            **{f"metric_{mid}": "0"},
        ),
    )
    assert saved.status_code == 303
    page = call(store, "/patients/{pid}/diary", who="p1", pid=1)
    html = page.body.decode()
    assert f'href="#metric-dialog-{mid}"' in html and "Энергия" in html
    assert "0/10" in html and "качество 0/10" in html
    with connect(path) as c:
        row = c.execute(
            "SELECT wellbeing,sleep,sleep_quality,note FROM diary WHERE patient_id=1"
        ).fetchone()
        assert tuple(row) == (0, 0.0, 0, "Сохранённая тестовая запись")
