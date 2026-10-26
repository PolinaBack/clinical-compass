"""Read-only editorial material, separate from manually verified official facts."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

REFERENCE = (
    Path(__file__).resolve().parents[1] / "references" / "medicines_editorial.json"
)
LEVELS = {
    "ПРОТИВОПОКАЗАНО": ("contraindicated", "Противопоказано по редакционной записи"),
    "ПОВЫШЕННЫЙ РИСК": ("high", "Повышенная осторожность"),
    "ОЦЕНИТЬ": ("caution", "Требует оценки"),
    "НЕТ ДАННЫХ": ("unknown", "Данных недостаточно"),
}
TITLES = {
    "highlights": "Ключевое о выбранной форме",
    "pharmacokinetics": "Фармакокинетика",
}
# The supplied material mixes products in several descriptions. Do not silently
# transfer another product's excipients, expiry, manufacturer or doses to this one.
DISCREPANCIES = {
    "D01": "Регистрационный статус, срок годности и контакты требуют самостоятельной сверки по выбранной упаковке.",
    "D02": "Часть описания и справочная ссылка относятся к Ципралексу. Они не подтверждают состав и регистрацию препарата Пранафарм.",
    "D03": "В тексте упоминается Асентра и контакты KRKA, а выбрана форма Фармпроект. Их применимость к этой упаковке не подтверждена.",
    "D04": "В отдельных разделах упоминается ОЗОН, тогда как в метаданных указан другой производитель. Контакты и срок годности нужно сверить.",
    "D05": "Выбраны таблетки 100 мг Северной Звезды; описание состава относится к 50 мг, а ряд сведений — к Феварину. Не переносите их на выбранную упаковку.",
    "D06": "Выбраны обычные таблетки 37,5 мг. Разделы дозирования и состава также описывают пролонгированные капсулы Алвента 75 мг: это другая форма, схемы не взаимозаменяемы.",
    "D07": "Выбраны капсулы 60 мг Канонфарма. Описание состава дано для 30 мг, а отдельные сведения относятся к Дулоксенте/KRKA.",
    "D08": "Часть сведений о сроке годности и контактах относится к Каликсте/Belupo; применимость к форме Канонфарма не подтверждена.",
    "D09": "Выбран Агомелатин ОЗОН, но часть справочных сведений относится к Вальдоксану/Servier. Указание прежней сверки не является проверкой в этом приложении.",
    "D10": "Выбраны таблетки 10 мг, а описание состава приведено для 20 мг и другой оболочки. Сверьте состав именно выбранной дозировки.",
    "D11": "Регистрационный статус, полный состав, срок годности и контакты требуют сверки по конкретной упаковке Спитомина.",
    "D12": "В метаданных указан Канонфарма, но часть текста относится к Фармпроекту. Контакты и срок годности нельзя переносить между производителями.",
}


@lru_cache(maxsize=1)
def data():
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def neutral_text(text):
    """No link/name of the supplied specification in product UI; no new verification claim."""
    for a, b in [
        (
            "Предупреждение сверено дополнительно по опубликованной инструкции Росздравнадзора.",
            "Актуальная инструкция и прежнее указание на дополнительную сверку в приложении не подтверждены.",
        ),
        ("Согласно присланному листку-вкладышу", "В редакционном описании"),
        ("В присланной инструкции", "В редакционном описании"),
        ("По присланной инструкции", "По редакционному описанию"),
        ("в присланной инструкции", "в редакционном описании"),
        ("по присланной инструкции", "по редакционному описанию"),
        ("в представленной инструкции", "в редакционном описании"),
        ("представленных инструкциях", "редакционных описаниях"),
        ("в присланном материале", "в редакционной записи"),
        ("В присланных материалах", "В редакционных записях"),
        ("присланные инструкции", "редакционное описание инструкций"),
        ("присланная инструкция", "редакционное описание инструкции"),
        ("в проверенной карточке", "в редакционной карточке"),
    ]:
        text = text.replace(a, b)
    return text


def card(code):
    source = data()["cards"].get(code)
    if not source:
        return None
    value = {
        **source,
        "discrepancy": DISCREPANCIES[code],
        "revision": data()["version"],
    }
    value["sections"] = [
        {
            **s,
            "title": TITLES.get(s["key"], s["title"]),
            "text": neutral_text(s["text"]),
        }
        for s in source["sections"]
    ]
    value["sections"].insert(
        10,
        dict(
            key="pharmacokinetics",
            title=TITLES["pharmacokinetics"],
            text="Отдельного описания фармакокинетики в редакционной базе нет. Сверьте актуальную ОХЛП конкретной формы.",
        ),
    )
    return value


def enrich(drug):
    if drug.get("reference_override"):
        return {
            **drug,
            **{
                k: drug.get(k) or "Не указано"
                for k in (
                    "inn",
                    "manufacturer",
                    "form",
                    "strength",
                    "package",
                    "registration",
                    "atc",
                    "group",
                )
            },
            "reference": drug["reference_override"],
        }
    info = card(drug["code"])
    if not info:
        return {
            **drug,
            "manufacturer": "Не указан",
            "strength": "Не уточнена",
            "package": "Не уточнено",
            "registration": "Не уточнён",
            "reference": dict(
                sections=[],
                reactions=[],
                links=[],
                reference_date="Не указана",
                discrepancy="Для этой записи нет расширенного редакционного описания. Исходная запись и отдельно проверенные документы сохранены.",
            ),
        }
    return {
        **drug,
        **{
            k: info[k]
            for k in (
                "name",
                "inn",
                "manufacturer",
                "form",
                "strength",
                "package",
                "registration",
                "atc",
            )
        },
        "reference": info,
        "aliases": " · ".join(filter(None, [drug.get("aliases"), drug["name"]])),
    }


def snapshot(drug):
    value = {
        k: drug.get(k, "")
        for k in (
            "code",
            "name",
            "inn",
            "manufacturer",
            "form",
            "strength",
            "package",
            "registration",
        )
    }
    value.update(
        reference_version=data()["version"],
        reference_sha256=hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
        catalogue_import=drug.get("_import"),
        status="editorial_unverified",
    )
    return value


def pair(a, b):
    raw = data()["pairs"].get("|".join(sorted((a, b))))
    if not raw:
        return dict(
            level="unknown",
            title="Данных недостаточно",
            basis="unknown",
            mechanism="Индивидуальное правило отсутствует.",
            effects="Отсутствие записи не подтверждает безопасность.",
            action="Сверьте актуальные инструкции всех препаратов.",
            origin="Нет записи в редакционной базе",
        )
    level, title = LEVELS[raw["level"]]
    value = {**raw, "level": level, "title": title}
    if raw["basis"] == "matrix":
        value.update(
            mechanism="Индивидуальный механизм этой пары в базе не описан.",
            effects=(
                "Отметка в матрице требует оценки; она не доказывает эффект для конкретной пары."
                if level != "unknown"
                else "Данных о конкретной паре недостаточно. Это не означает отсутствие взаимодействия."
            ),
            action="Проверьте актуальные ОХЛП, все лекарства, дозы, сопутствующие заболевания и необходимые показатели.",
            origin=(
                "Классовая отметка матрицы · эвристика, не подтверждённое индивидуальное правило"
                if level != "unknown"
                else "Недостаточное покрытие редакционной базы"
            ),
        )
    return {k: neutral_text(v) if isinstance(v, str) else v for k, v in value.items()}
