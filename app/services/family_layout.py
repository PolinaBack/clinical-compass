"""A shared, explicit family layout: decorative leaves and medical symbols."""

import re

RELATIONS = {
    "mother": "Мама",
    "father": "Папа",
    "brother": "Брат",
    "sister": "Сестра",
    "sibling": "Брат / сестра",
    "child": "Ребёнок",
    "other": "Близкий",
    "custom": "Близкий",
    "self": "Я",
}
SLOT_LABELS = {
    "paternal_grandfather": "Дедушка",
    "paternal_grandmother": "Бабушка",
    "maternal_grandfather": "Дедушка",
    "maternal_grandmother": "Бабушка",
    "father": "Папа",
    "mother": "Мама",
}


def name(person, fallback):
    text = (
        re.sub(r"^демо[\s-]+", "", person.get("name", ""), flags=re.I).strip()
        if person
        else ""
    )
    return (
        fallback if not text or text.casefold() in ("родственник", "близкий") else text
    )


def layout(relatives, selected=""):
    by_slot = {r["slot"]: r for r in relatives if r.get("slot")}
    archived_parents = [
        r
        for r in relatives
        if not r.get("slot") and r["relation"] in ("mother", "father")
    ]
    extras = [
        r
        for r in relatives
        if not r.get("slot") and r["relation"] not in ("self", "mother", "father")
    ]
    siblings = [r for r in extras if r["relation"] in ("brother", "sister", "sibling")]
    other = [r for r in extras if r not in siblings]
    width = max(1200, 260 * (len(siblings) + 1) + 120)
    mid = width / 2
    nodes = []
    lines = []

    def node(key, label, x, y, person=None, subtitle="", generation=0):
        conditions = person.get("conditions", []) if person else []
        match = [c for c in conditions if c["label"] == selected] if selected else []
        sex = (person or {}).get("sex", "unknown")
        if sex == "unknown":
            if (
                key in ("father", "paternal_grandfather", "maternal_grandfather")
                or (person or {}).get("relation") == "brother"
            ):
                sex = "male"
            elif (
                key in ("mother", "paternal_grandmother", "maternal_grandmother")
                or (person or {}).get("relation") == "sister"
            ):
                sex = "female"
        about = (person or {}).get("about", "") or (person or {}).get("description", "")
        notes = "; ".join(c["label"] for c in conditions[:2]) or about[:100]
        filled = bool(
            person
            and (
                notes
                or person.get("answer_kind", "unknown")
                not in ("unknown", "deferred", "declined")
            )
        )
        n = dict(
            key=key,
            label=label,
            subtitle=subtitle,
            display_name=name(person, label),
            px=x,
            py=y,
            x=x / width * 100,
            art_y=y,
            person=person,
            filled=filled,
            brief=notes,
            sex=sex,
            age=(person or {}).get("age"),
            deceased=(person or {}).get("deceased", 0),
            affected=bool(match),
            uncertain=bool(any(c["uncertain"] or c["kind"] != "known" for c in match)),
            age_text=match[0]["age_text"] if match else "",
            unknown=(not match) if selected else (not person or not conditions),
            status=(
                "Ответ добавлен"
                if filled
                else "Черновик" if person else "Пока не заполнено"
            ),
            generation=generation,
        )
        nodes.append(n)
        return n

    # Parent and grandparent pairs have real junctions and do not cross the labels.
    for index, (key, x, subtitle) in enumerate(
        [
            ("paternal_grandfather", mid - 380, "Папин папа"),
            ("paternal_grandmother", mid - 140, "Папина мама"),
            ("maternal_grandfather", mid + 140, "Мамин папа"),
            ("maternal_grandmother", mid + 380, "Мамина мама"),
        ]
    ):
        node(key, SLOT_LABELS[key], x, 95, by_slot.get(key), subtitle, 1)
    father = node(
        "father", "Папа", mid - 220, 365, by_slot.get("father"), "Отцовская ветвь", 2
    )
    mother = node(
        "mother", "Мама", mid + 220, 365, by_slot.get("mother"), "Материнская ветвь", 2
    )
    for x1, x2, target in [
        (mid - 380, mid - 140, mid - 220),
        (mid + 140, mid + 380, mid + 220),
    ]:
        center = (x1 + x2) / 2
        lines.extend([f"M{x1+40} 95 H{x2-40}", f"M{center} 95 V255 H{target} V325"])
    lines.append(f'M{father["px"]+40} 365 H{mother["px"]-40}')
    positions = {n["person"]["id"]: n for n in nodes if n["person"]}
    center_nodes = []
    # Proband stays at the centre; brother and sister are separate records.
    children = [*siblings[: len(siblings) // 2], None, *siblings[len(siblings) // 2 :]]
    for i, p in enumerate(children):
        x = mid + (i - (len(children) - 1) / 2) * 250
        if p:
            n = node(
                "custom-" + str(p["id"]),
                RELATIONS.get(p["relation"], "Близкий"),
                x,
                655,
                p,
                generation=3,
            )
        else:
            n = node("self", "Я", x, 655, None, "Пробанд", 3)
        center_nodes.append(n)
    self_node = next(n for n in nodes if n["key"] == "self")
    bar_y = 555
    lines.append(f"M{mid} 365 V{bar_y}")
    combined = [
        n for n in center_nodes if not n["person"] or not n["person"].get("parent_id")
    ]
    xs = [mid, *[n["px"] for n in combined]]
    if len(xs) > 1:
        lines.append(f"M{min(xs)} {bar_y} H{max(xs)}")
    for n in center_nodes:
        parent = positions.get(n["person"].get("parent_id")) if n["person"] else None
        if parent:
            # A selected parent is explicit: do not imply a second parent.
            lines.append(
                f'M{parent["px"]} {parent["py"]+120} V{bar_y-25} H{n["px"]} V{n["py"]-40}'
            )
        else:
            lines.append(f'M{n["px"]} {bar_y} V{n["py"]-40}')
    positions = {n["person"]["id"]: n for n in nodes if n["person"]}
    pending = list(other)
    placed = []
    # Only explicit parent_id or a child's relation supplies a descent line.
    while pending:
        batch = [
            p for p in pending if not p.get("parent_id") or p["parent_id"] in positions
        ]
        if not batch:
            batch = list(pending)
        for p in batch:
            parent = positions.get(p.get("parent_id"))
            if parent:
                generation = parent["generation"] + 1 if parent["generation"] else 0
            else:
                generation = 4 if p["relation"] == "child" else 0
            y = {0: (parent["py"] + 300 if parent else 955), 1: 95, 2: 365, 3: 655}.get(
                generation, 955 + (generation - 4) * 300
            )
            row = [n for n in nodes if n["py"] == y]
            x = max([n["px"] for n in row], default=-100) + 250
            label = p.get("custom_relation") or RELATIONS.get(p["relation"], "Близкий")
            n = node("custom-" + str(p["id"]), label, x, y, p, generation=generation)
            positions[p["id"]] = n
            placed.append(n)
            pending.remove(p)
            if not parent and p["relation"] == "child":
                parent = self_node
            if parent:
                lines.append(
                    f'M{parent["px"]} {parent["py"]+160} V{y-100} H{x} V{y-40}'
                )
    width = max(width, max(n["px"] for n in nodes) + 140)
    height = max(n["py"] for n in nodes) + 190
    for n in nodes:
        n["x"] = n["px"] / width * 100
    roman = {1: "I", 2: "II", 3: "III", 4: "IV"}
    for gen in sorted({n["generation"] for n in nodes}):
        for i, n in enumerate(
            sorted([n for n in nodes if n["generation"] == gen], key=lambda n: n["px"]),
            1,
        ):
            n["number"] = (
                f"{roman.get(gen,gen)}-{i}" if gen else "Поколение не уточнено"
            )
    for n in nodes:
        n["y"] = n["py"] / height * 100
    return dict(
        family_archived_parents=archived_parents,
        family_nodes=nodes,
        family_extras=extras,
        family_filled=sum(n["filled"] for n in nodes if n["key"] in SLOT_LABELS),
        family_height=height,
        family_width=width,
        family_self_y=self_node["y"],
        family_connections=lines,
        selected_condition=selected,
    )
