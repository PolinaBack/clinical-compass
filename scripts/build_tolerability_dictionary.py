"""Developer-only verification/build. The app needs neither PDF nor pdftotext."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import openpyxl


def build(pdf):
    book = (
        Path(__file__).resolve().parents[1]
        / "docs/inputs/2026-10-21/Побочные эффекты по категориям.xlsx"
    )
    w = openpyxl.load_workbook(book, read_only=True, data_only=True)
    options = [
        dict(
            code=r[0],
            system=r[1],
            medical=r[2],
            label=r[3],
            allowed=str(r[7]).startswith("ДА"),
        )
        for r in list(w["Словарь_вопросов"].values)[3:]
        if r[0]
    ]
    rows = list(w["Реакции_по_препаратам"].values)[3:]
    text = subprocess.check_output(["pdftotext", "-layout", str(pdf), "-"], text=True)
    text = re.sub(
        r"^КЛИНИЧЕСКИЙ КОМПАС / справочная версия 11.*$", "", text, flags=re.M
    ).replace("\f", "\n")
    assert (
        len(rows) == 468
        and len(options) == 163
        and sum(o["allowed"] for o in options) == 138
    )
    assert {o["code"] for o in options if o["allowed"]} == set(
        re.findall(r"^\s*(S\d{3})\.", text, re.M)
    )
    records_text = text[
        text.index("02 · Полный список") : text.index("03 · Предпочтения")
    ]
    systems = {r[3] for r in rows}
    pattern = r"^\s*(?:(?P<header>[^\n]+?) · \d+ записей|(?P<number>\d{3})\.\s+(?P<label>.+?)\s+·\s+\[(?P<frequency>[^\]]+)\])\s*$"
    actual = []
    current_system = drug = None
    for match in re.finditer(pattern, records_text, re.M):
        if match["header"]:
            header = match["header"]
            if header in systems:
                current_system = header
            elif " / " in header:
                drug = header.split(" / ")[1]
        else:
            actual.append(
                (
                    int(match["number"]),
                    drug,
                    current_system,
                    match["label"],
                    match["frequency"],
                )
            )
    assert [r[0] for r in actual] == list(range(1, 469)), len(actual)
    expected = []
    for row in rows:
        medical, friendly = row[4], row[5]
        label = (
            friendly
            if friendly.casefold() == medical.casefold()
            else f"{friendly} ({medical})"
        )
        expected.append((row[1], row[3], label, row[6]))
    assert Counter(r[1:] for r in actual) == Counter(
        expected
    ), "PDF/Excel entries differ"
    w.close()
    return dict(
        version="tolerability-2026-10-25",
        specification_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        options=options,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    args.output.write_text(
        json.dumps(build(args.pdf), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "Verified: all 468 numbered PDF entries, 15 systems and 138 eligible dictionary codes match the original workbook."
    )
