"""Descriptive family history. No diagnoses are inferred and no risk is scored."""

from fastapi import Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from .storage import now, audit

SLOTS = {
    "maternal_grandmother": ("Бабушка", "Мамина мама", "other", 18, 24),
    "maternal_grandfather": ("Дедушка", "Мамин папа", "other", 38, 14),
    "paternal_grandmother": ("Бабушка", "Папина мама", "other", 62, 14),
    "paternal_grandfather": ("Дедушка", "Папин папа", "other", 82, 24),
    "mother": ("Мама", "Материнская ветвь", "mother", 33, 59),
    "father": ("Папа", "Отцовская ветвь", "father", 67, 59),
}
KINDS = [
    ("known", "Есть заболевания, могу вспомнить"),
    ("signs", "Помню признаки, диагноз не знаю"),
    ("not_heard", "О заболеваниях не слышал(а)"),
    ("unknown", "Не знаю"),
    ("declined", "Не хочу отвечать"),
    ("deferred", "Вернусь к этому позже"),
]
AGE_KINDS = [
    "Возраст, когда сообщили диагноз",
    "Возраст, когда заметили изменения",
    "Не знаю",
]
SOURCES = [
    "Со слов родственников",
    "Со слов других близких",
    "Из документов",
    "Мои наблюдения",
    "Не знаю",
]
RELATION_CHOICES = [
    ("mother", "Мама"),
    ("father", "Папа"),
    ("brother", "Брат"),
    ("sister", "Сестра"),
    ("grandmother", "Бабушка · линия не уточнена"),
    ("grandfather", "Дедушка · линия не уточнена"),
    ("maternal_grandmother", "Бабушка по маминой линии"),
    ("maternal_grandfather", "Дедушка по маминой линии"),
    ("paternal_grandmother", "Бабушка по папиной линии"),
    ("paternal_grandfather", "Дедушка по папиной линии"),
    ("uncle", "Дядя"),
    ("aunt", "Тётя"),
    ("child", "Ребёнок"),
    ("other", "Другой близкий"),
    ("custom", "Другая / Своя категория"),
]


def creation_context(relatives):
    occupied = {r.get("slot") for r in relatives}
    parents = {
        r["relation"] for r in relatives if r["relation"] in ("mother", "father")
    }
    return dict(
        family_relation_choices=[
            (key, label)
            for key, label in RELATION_CHOICES
            if key not in occupied and key not in parents
        ]
    )


def person_fields(form):
    sex = str(form.get("sex", "unknown"))
    age = str(form.get("age", "")).strip()
    about = str(form.get("about", "")).strip()
    if (
        sex not in ("male", "female", "unknown")
        or age
        and (not age.isdigit() or not 0 <= int(age) <= 130)
        or len(about) > 3000
    ):
        raise HTTPException(422, "Проверьте пол, возраст и заметку о близком")
    return sex, int(age) if age else None, int(form.get("deceased") == "yes"), about


CONDITION_KINDS = {
    "known": "Знаю название заболевания",
    "signs": "Помню признаки, диагноз не знаю",
    "unspecified": "Сведения из прежней записи",
}


def canonical_source(source):
    # Keep existing stored text intact; display and new saves use the photo's label.
    return "Со слов родственников" if source == "Со слов родственника" else source


def family_tree(relatives, selected=""):
    from .services.family_layout import layout, patient_layout

    context = layout(relatives, selected)
    return {
        **context,
        **patient_layout(context["family_nodes"]),
        **creation_context(relatives),
    }


def register_family(app, db, account, access, csrf_check, render, patient_context):
    def find(c, pid, key):
        if key in SLOTS:
            return c.execute(
                "SELECT * FROM relatives WHERE patient_id=? AND slot=?", (pid, key)
            ).fetchone()
        if key.isdigit() or (key.startswith("custom-") and key[7:].isdigit()):
            row = c.execute(
                "SELECT * FROM relatives WHERE patient_id=? AND id=?",
                (pid, int(key if key.isdigit() else key[7:])),
            ).fetchone()
            if row:
                return row
        raise HTTPException(404, "Родственник не найден")

    @app.get("/patients/{pid}/profile/family/add")
    def add_page(request: Request, pid: int, relation: str = ""):
        access(request, pid, True)
        context = patient_context(request, pid)
        choices = creation_context(context["relatives"])
        allowed = dict(choices["family_relation_choices"])
        return render(
            request,
            "family_add.html",
            section="family",
            new_relation=relation if relation in allowed else "",
            **choices,
            **context,
        )

    @app.post("/patients/{pid}/relatives")
    async def add_relative(request: Request, pid: int):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        relation = str(form.get("relation", ""))
        custom = str(form.get("custom_relation", "")).strip()
        name = str(form.get("name", "")).strip()
        if (
            relation not in dict(RELATION_CHOICES)
            and relation not in ("self", "sibling")
            or len(name) > 80
            or len(custom) > 80
        ):
            raise HTTPException(422, "Проверьте родство и имя близкого")
        if relation == "custom" and (
            not custom or custom.casefold() in ("мама", "папа", "мать", "отец")
        ):
            raise HTTPException(
                422, "Укажите свою категорию; для матери и отца используйте их листья"
            )
        sex, age, deceased, about = person_fields(form)
        label = (
            SLOTS[relation][0]
            if relation in SLOTS
            else {**dict(RELATION_CHOICES), "self": "Я", "sibling": "Близкий"}.get(
                relation
            )
        )
        if relation == "custom":
            label = custom
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            if (
                relation in SLOTS
                and c.execute(
                    "SELECT 1 FROM relatives WHERE patient_id=? AND (slot=? OR (? IN ('mother','father') AND relation=?))",
                    (pid, relation, relation, relation),
                ).fetchone()
            ):
                raise HTTPException(
                    409, "Этот родственник уже есть. Откройте существующий лист"
                )
            parent_text = str(form.get("parent_id", "")).strip()
            parent = None
            if parent_text:
                if (
                    not parent_text.isdigit()
                    or len(parent_text) > 18
                    or not c.execute(
                        "SELECT 1 FROM relatives WHERE id=? AND patient_id=?",
                        (int(parent_text), pid),
                    ).fetchone()
                ):
                    raise HTTPException(422, "Родитель должен принадлежать этой семье")
                parent = int(parent_text)
            rid = c.execute(
                "INSERT INTO relatives(patient_id,name,relation,parent_id,slot) VALUES(?,?,?,?,?)",
                (
                    pid,
                    name or label,
                    SLOTS[relation][2] if relation in SLOTS else relation,
                    parent,
                    relation if relation in SLOTS else None,
                ),
            ).lastrowid
            c.execute(
                "INSERT INTO family_person_meta(relative_id,custom_relation,sex,age,deceased,updated_by,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    rid,
                    custom if relation == "custom" else "",
                    sex,
                    age,
                    deceased,
                    account(request)["id"],
                    now(),
                ),
            )
            c.execute(
                "INSERT INTO family_details(relative_id,about,updated_at) VALUES(?,?,?)",
                (rid, about, now()),
            )
            audit(c, account(request)["id"], "family_member_added", pid, rid)
        key = relation if relation in SLOTS else f"custom-{rid}"
        return RedirectResponse(
            f"/patients/{pid}/profile/family#leaf-{key}", status_code=303
        )

    @app.get("/patients/{pid}/profile/family/summary")
    def family_summary(request: Request, pid: int):
        patient_context(request, pid)
        return RedirectResponse(f"/patients/{pid}/profile/family", status_code=303)

    @app.get("/patients/{pid}/profile/family/member/{key}")
    def member_page(request: Request, pid: int, key: str, tab: str = "about"):
        context = patient_context(request, pid)
        if tab not in ["about", "knowledge", "explanations"]:
            raise HTTPException(404, "Раздел не найден")
        with db() as c:
            row = find(c, pid, key)
            person = dict(row) if row else dict(name=SLOTS[key][0])
            details = c.execute(
                "SELECT * FROM family_details WHERE relative_id=?", (person.get("id"),)
            ).fetchone()
            meta = c.execute(
                "SELECT * FROM family_person_meta WHERE relative_id=?",
                (person.get("id"),),
            ).fetchone()
            if meta:
                person.update(dict(meta))
            conditions = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM family_conditions WHERE relative_id=? ORDER BY id",
                    (person.get("id"),),
                )
            ]
        for condition in conditions:
            condition["source"] = canonical_source(condition["source"])
        tree = family_tree(context["relatives"])
        node = next((n for n in tree["family_nodes"] if n["key"] == key), None)
        return render(
            request,
            "family_member.html",
            section="family",
            member_key=key,
            person=person,
            member_label=node["label"] if node else person["name"],
            member_relation={"mother": "Мать", "father": "Отец"}.get(
                key, node["subtitle"] if node else "Близкий"
            ),
            member_status=(
                node["status"]
                if node
                else "Ответ добавлен" if details else "Пока не заполнено"
            ),
            details=dict(details) if details else {},
            conditions=conditions,
            tab=tab,
            kinds=KINDS,
            age_kinds=AGE_KINDS,
            info_sources=SOURCES,
            **tree,
            **context,
        )

    @app.post("/patients/{pid}/family/{key}")
    async def save_member(request: Request, pid: int, key: str):
        access(request, pid, True)
        form = await request.form()
        csrf_check(request, str(form.get("csrf", "")))
        tab = str(form.get("tab", ""))
        if tab not in ["about", "knowledge", "explanations"]:
            raise HTTPException(422, "Выберите раздел")
        fields = {
            k: str(form.get(k, "")).strip()
            for k in ["name", "about", "answer_kind", "description", "explanation"]
        }
        if any(len(v) > 3000 for v in fields.values()) or len(fields["name"]) > 80:
            raise HTTPException(422, "Слишком длинный текст")
        if tab == "knowledge" and fields["answer_kind"] not in dict(KINDS):
            raise HTTPException(422, "Выберите, что известно")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = find(c, pid, key)
            if not row:
                label, subtitle, relation, x, y = SLOTS[key]
                rid = c.execute(
                    "INSERT INTO relatives(patient_id,name,relation,slot) VALUES(?,?,?,?)",
                    (pid, fields["name"] or label, relation, key),
                ).lastrowid
            else:
                rid = row["id"]
            c.execute(
                "INSERT OR IGNORE INTO family_details(relative_id,updated_at) VALUES(?,?)",
                (rid, now()),
            )
            if tab == "about":
                sex = str(form.get("sex", "unknown"))
                age = str(form.get("age", "")).strip()
                if (
                    sex not in ("male", "female", "unknown")
                    or age
                    and (not age.isdigit() or not 0 <= int(age) <= 130)
                ):
                    raise HTTPException(422, "Проверьте пол и возраст близкого")
                c.execute(
                    "INSERT INTO family_person_meta(relative_id,sex,age,deceased,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(relative_id) DO UPDATE SET sex=excluded.sex,age=excluded.age,deceased=excluded.deceased,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                    (
                        rid,
                        sex,
                        int(age) if age else None,
                        int(form.get("deceased") == "yes"),
                        account(request)["id"],
                        now(),
                    ),
                )
                if not fields["name"]:
                    raise HTTPException(422, "Укажите имя или родство")
                c.execute(
                    "UPDATE relatives SET name=? WHERE id=?", (fields["name"], rid)
                )
                c.execute(
                    "UPDATE family_details SET about=?,updated_at=? WHERE relative_id=?",
                    (fields["about"], now(), rid),
                )
                if row and row["relation"] == "custom" and "custom_relation" in form:
                    custom = str(form.get("custom_relation", "")).strip()
                    if not 1 <= len(custom) <= 80 or custom.casefold() in (
                        "мама",
                        "папа",
                        "мать",
                        "отец",
                    ):
                        raise HTTPException(
                            422, "Укажите свою категорию родства, до 80 символов"
                        )
                    c.execute(
                        "UPDATE family_person_meta SET custom_relation=? WHERE relative_id=?",
                        (custom, rid),
                    )
            elif tab == "knowledge":
                c.execute(
                    "UPDATE family_details SET answer_kind=?,description=?,updated_at=? WHERE relative_id=?",
                    (fields["answer_kind"], fields["description"], now(), rid),
                )
            else:
                c.execute(
                    "UPDATE family_details SET explanation=?,discuss=?,updated_at=? WHERE relative_id=?",
                    (
                        fields["explanation"],
                        int(form.get("discuss") == "true"),
                        now(),
                        rid,
                    ),
                )
            audit(c, account(request)["id"], "family_" + tab + "_saved", pid, rid)
        if form.get("return_to") == "tree":
            return RedirectResponse(
                f"/patients/{pid}/profile/family#leaf-{key}", status_code=303
            )
        return RedirectResponse(
            f"/patients/{pid}/profile/family/member/{key}?tab={tab}&saved=1",
            status_code=303,
        )

    @app.post("/patients/{pid}/family/{key}/conditions")
    def condition(
        request: Request,
        pid: int,
        key: str,
        csrf: str = Form(...),
        label: str = Form(..., min_length=1, max_length=200),
        description: str = Form("", max_length=1000),
        kind: str = Form("unspecified"),
        age_kind: str = Form("Не знаю"),
        age_text: str = Form("", max_length=80),
        source: str = Form("Не знаю"),
        uncertain: bool = Form(False),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        source = canonical_source(source)
        if (
            not label.strip()
            or age_kind not in AGE_KINDS
            or source not in SOURCES
            or kind not in CONDITION_KINDS
        ):
            raise HTTPException(422, "Проверьте описание, возраст и источник сведений")
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = find(c, pid, key)
            if not row:
                name, subtitle, relation, x, y = SLOTS[key]
                rid = c.execute(
                    "INSERT INTO relatives(patient_id,name,relation,slot) VALUES(?,?,?,?)",
                    (pid, name, relation, key),
                ).lastrowid
                row = c.execute("SELECT * FROM relatives WHERE id=?", (rid,)).fetchone()
            rid = row["id"]
            c.execute(
                "INSERT INTO family_conditions(relative_id,label,description,age_kind,age_text,source,uncertain,kind) VALUES(?,?,?,?,?,?,?,?)",
                (
                    rid,
                    label.strip(),
                    description.strip(),
                    age_kind,
                    age_text.strip(),
                    source,
                    int(uncertain),
                    kind,
                ),
            )
            audit(c, account(request)["id"], "family_condition_added", pid, rid)
        key = row["slot"] or f"custom-{rid}"
        return RedirectResponse(
            f"/patients/{pid}/profile/family/member/{key}?tab=knowledge",
            status_code=303,
        )

    @app.post("/patients/{pid}/family/conditions/{cid}/update")
    def update_condition(
        request: Request,
        pid: int,
        cid: int,
        csrf: str = Form(...),
        age_kind: str = Form(...),
        age_text: str = Form("", max_length=80),
        source: str = Form(...),
        uncertain: bool = Form(False),
    ):
        access(request, pid, True)
        csrf_check(request, csrf)
        source = canonical_source(source)
        if age_kind not in AGE_KINDS or source not in SOURCES:
            raise HTTPException(422, "Проверьте возраст и источник сведений")
        with db() as c:
            row = c.execute(
                "SELECT r.* FROM family_conditions f JOIN relatives r ON r.id=f.relative_id WHERE f.id=? AND r.patient_id=?",
                (cid, pid),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Сведения не найдены")
            c.execute(
                "UPDATE family_conditions SET age_kind=?,age_text=?,source=?,uncertain=? WHERE id=?",
                (age_kind, age_text.strip(), source, int(uncertain), cid),
            )
            audit(c, account(request)["id"], "family_condition_updated", pid, cid)
        key = row["slot"] or f"custom-{row['id']}"
        return RedirectResponse(
            f"/patients/{pid}/profile/family/member/{key}?tab=knowledge&condition_saved=1",
            status_code=303,
        )

    @app.post("/patients/{pid}/family/conditions/{cid}/delete")
    def remove_condition(request: Request, pid: int, cid: int, csrf: str = Form(...)):
        access(request, pid, True)
        csrf_check(request, csrf)
        with db() as c:
            row = c.execute(
                "SELECT r.* FROM family_conditions f JOIN relatives r ON r.id=f.relative_id WHERE f.id=? AND r.patient_id=?",
                (cid, pid),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Сведения не найдены")
            c.execute("DELETE FROM family_conditions WHERE id=?", (cid,))
            audit(c, account(request)["id"], "family_condition_removed", pid, row["id"])
        key = row["slot"] or f"custom-{row['id']}"
        return RedirectResponse(
            f"/patients/{pid}/profile/family/member/{key}?tab=knowledge",
            status_code=303,
        )
