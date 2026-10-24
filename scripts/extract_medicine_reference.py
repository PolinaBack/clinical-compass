"""Build the checked-in editorial reference from the supplied PDF (requires pdftotext).

Developer utility only; the application never parses or serves the specification.
Run: python -m scripts.extract_medicine_reference input.pdf output.json
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

HEADINGS = [
    ("highlights", "ВАЖНО: данные из инструкции присланной формы"),
    ("group", "Группа"),
    ("indications", "Показания"),
    ("doses", "Способ применения и дозы"),
    ("composition", "Состав (справочное описание)"),
    ("contraindications", "Противопоказания"),
    ("warnings", "Особые указания"),
    ("adverse", "Побочные действия"),
    ("interactions", "Взаимодействия с другими лекарствами"),
    ("pharmacodynamics", "Фармакодинамика"),
    ("storage", "Условия хранения"),
    ("shelf_life", "Срок годности"),
    ("pharmacovigilance", "Фармаконадзор"),
]


def paragraphs(value):
    return "\n\n".join(
        " ".join(p.split()) for p in re.split(r"\n\s*\n", value.strip()) if p.strip()
    )


def extract(path):
    text = subprocess.check_output(["pdftotext", "-layout", str(path), "-"], text=True)
    text = re.sub(
        r"^КЛИНИЧЕСКИЙ КОМПАС • v10 / учебный прототип.*$", "", text, flags=re.M
    ).replace("\f", "\n")
    starts = list(re.finditer(r"ЛЕКАРСТВЕННАЯ КАРТОЧКА (\d{2}) / 12", text))
    cards = {}
    for n, start in enumerate(starts):
        code = "D" + start[1]
        body = text[
            start.end() : (
                starts[n + 1].start()
                if n + 1 < len(starts)
                else text.index("05 Проверка сочетаний")
            )
        ]
        lines = [s.strip() for s in body.splitlines() if s.strip()]

        def after(label):
            return lines[lines.index(label) + 1]

        form_dose = after("Доза / упаковка").split(", ")
        card = dict(
            code=code,
            name=lines[0],
            inn=after("МНН"),
            manufacturer=after("Производитель (из пользовательского текста)"),
            form=after("Форма"),
            strength=form_dose[0],
            package=form_dose[1],
            registration=after("Регистрационный номер, указанный пользователем"),
            atc=after("ATX"),
            reference_date="2026-10-21",
        )
        content = body[: body.index("Нежелательные реакции по системам")]
        sections = []
        for i, (key, label) in enumerate(HEADINGS):
            pos = re.search(r"^\s*" + re.escape(label) + r"\s*$", content, re.M)
            end = (
                re.search(
                    r"^\s*" + re.escape(HEADINGS[i + 1][1]) + r"\s*$", content, re.M
                ).start()
                if i + 1 < len(HEADINGS)
                else len(content)
            )
            sections.append(
                dict(key=key, title=label, text=paragraphs(content[pos.end() : end]))
            )
        registry = body[
            body.index("Нежелательные реакции по системам") : body.index(
                "Источники карточки и статус"
            )
        ]
        expected = int(re.search(r"по системам \((\d+)\)", registry)[1])
        groups = list(re.finditer(r"^\s*([^\n]+?) \((\d+)\):\s*$", registry, re.M))
        reactions = []
        for i, group in enumerate(groups):
            contents = " ".join(
                registry[
                    group.end() : (
                        groups[i + 1].start() if i + 1 < len(groups) else len(registry)
                    )
                ].split()
            )
            entries = [x.strip() for x in contents.split(";") if x.strip()]
            assert len(entries) == int(group[2]), (code, group[1], entries)
            for entry in entries:
                term, frequency = entry.split(" — ")
                reactions.append(
                    dict(system=group[1].strip(), term=term, frequency=frequency)
                )
        assert len(reactions) == expected, (code, len(reactions), expected)
        card.update(
            sections=sections,
            reactions=reactions,
            links=re.findall(
                r"https://\S+", body[body.index("Источники карточки и статус") :]
            ),
        )
        cards[code] = card
    names = {card["name"]: code for code, card in cards.items()}
    matrix = text[
        text.index("05 Проверка сочетаний") : text.index(
            "Конкретные правила взаимодействий"
        )
    ]
    pairs = {}
    for line in matrix.splitlines():
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) == 3 and cols[0] in names and cols[1] in names:
            key = "|".join(sorted((names[cols[0]], names[cols[1]])))
            assert key not in pairs
            pairs[key] = dict(level=cols[2], basis="matrix")
    rules = text[
        text.index("Конкретные правила взаимодействий") : text.index(
            "06 Для разработчика"
        )
    ]
    pattern = (
        r"^\s*(.+?) \+ (.+?) · (ПРОТИВОПОКАЗАНО|ПОВЫШЕННЫЙ РИСК|ОЦЕНИТЬ|НЕТ ДАННЫХ)\s*$"
    )
    rule_heads = list(re.finditer(pattern, rules, re.M))
    for i, head in enumerate(rule_heads):
        key = "|".join(sorted((names[head[1]], names[head[2]])))
        assert pairs[key]["level"] == head[3]
        block = rules[
            head.end() : (
                rule_heads[i + 1].start() if i + 1 < len(rule_heads) else len(rules)
            )
        ]
        fields = [
            ("mechanism", "Механизм:"),
            ("effects", "Возможные эффекты:"),
            ("action", "Что проверить:"),
            ("origin", "Источник правила:"),
        ]
        for j, (field, label) in enumerate(fields):
            a = block.index(label) + len(label)
            b = block.index(fields[j + 1][1]) if j + 1 < len(fields) else len(block)
            pairs[key][field] = paragraphs(block[a:b])
        pairs[key]["basis"] = "editorial_rule"
    assert len(cards) == 12 and sum(len(c["reactions"]) for c in cards.values()) == 468
    assert len(pairs) == 66 and len(rule_heads) == 21, (len(pairs), len(rule_heads))
    return dict(
        version="editorial-2026-10-25",
        specification_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        cards=cards,
        pairs=pairs,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(extract(args.input), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
