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
CONDITION_KINDS = {
    "known": "Знаю название заболевания",
    "signs": "Помню признаки, диагноз не знаю",
    "unspecified": "Сведения из прежней записи",
}


def canonical_source(source):
    # Keep existing stored text intact; display and new saves use the photo's label.
    return "Со слов родственников" if source == "Со слов родственника" else source


def family_tree(relatives):
    by_slot = {r["slot"]: r for r in relatives if r.get("slot")}
    extras = [r for r in relatives if not r.get("slot") and r["relation"] != "self"]
    height = 760 + ((len(extras) + 3) // 4) * 160
    nodes = []

    def add_node(key, label, subtitle, x, y, person):
        filled = bool(
            person
            and (
                person.get("condition_count", 0)
                or person.get("answer_kind", "unknown")
                not in ["unknown", "deferred", "declined"]
            )
        )
        nodes.append(
            dict(
                key=key,
                label=label,
                subtitle=subtitle,
                x=x,
                y=y / height * 100,
                art_y=y,
                person=person,
                filled=filled,
                status=(
                    "Ответ добавлен"
                    if filled
                    else "Черновик" if person else "Пока не заполнено"
                ),
            )
        )

    for key, (label, subtitle, relation, x, y) in SLOTS.items():
        add_node(key, label, subtitle, x, y * 7.6, by_slot.get(key))
    relations = {
        "mother": "Мать",
        "father": "Отец",
        "sibling": "Брат / сестра",
        "child": "Ребёнок",
        "other": "Близкий",
    }
    for index, person in enumerate(extras):
        add_node(
            f"custom-{person['id']}",
            person["name"],
            relations.get(person["relation"], "Близкий"),
            16 + (index % 4) * 22,
            610 + (index // 4) * 160,
            person,
        )
    positions = {
        n["person"]["id"]: (n["x"] * 11, n["art_y"]) for n in nodes if n["person"]
    }
    connections = []
    for node in nodes[6:]:
        origin = positions.get(node["person"].get("parent_id"), (550, height - 75))
        connections.append(
            dict(
                x1=origin[0],
                y1=origin[1] + 40 if origin[1] < node["art_y"] else origin[1],
                x2=node["x"] * 11,
                y2=node["art_y"] + 50,
            )
        )
    return dict(
        family_nodes=nodes,
        family_extras=extras,
        family_filled=sum(n["filled"] for n in nodes[:6]),
        family_height=height,
        family_self_y=(height - 106) / height * 100,
        family_connections=connections,
    )


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

    @app.get("/patients/{pid}/profile/family/summary")
    def family_summary(request: Request, pid: int):
        context = patient_context(request, pid)
        with db() as c:
            conditions = [
                dict(row)
                for row in c.execute(
                    "SELECT f.* FROM family_conditions f JOIN relatives r ON r.id=f.relative_id WHERE r.patient_id=? ORDER BY f.id",
                    (pid,),
                )
            ]
        return render(
            request,
            "family_summary.html",
            section="family",
            kinds=dict(KINDS),
            conditions=conditions,
            **family_tree(context["relatives"]),
            **context,
        )

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
                if not fields["name"]:
                    raise HTTPException(422, "Укажите вымышленное имя или родство")
                c.execute(
                    "UPDATE relatives SET name=? WHERE id=?", (fields["name"], rid)
                )
                c.execute(
                    "UPDATE family_details SET about=?,updated_at=? WHERE relative_id=?",
                    (fields["about"], now(), rid),
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
