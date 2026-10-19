"""Presentation catalogue; unavailable instruments never enter scoring/assignments."""

from .scales import SCALES

TOOLS = [
    dict(
        key="safety6",
        name="C-SSRS",
        description="Беседа о безопасности. Доступна учебная адаптация по предоставленной статье.",
        active=True,
    ),
    dict(
        key="phq4",
        name="PHQ-4",
        description="Четыре вопроса о тревоге и настроении за две недели.",
        active=True,
    ),
    dict(
        key="phq9",
        name="PHQ-9",
        description="Девять вопросов о депрессивных симптомах за две недели.",
        active=True,
    ),
    dict(
        key="gad7",
        name="GAD-7",
        description="Семь вопросов о тревожных симптомах за две недели.",
        active=True,
    ),
    dict(
        key="phqads",
        name="PHQ-ADS",
        description="Пакет PHQ-9 и GAD-7 одной сессии. Две формы сохраняют отдельные результаты.",
        active=True,
    ),
    dict(
        key="hcl33",
        name="HCL-33",
        description="33 симптомных пункта о периодах подъёма и отдельные вопросы о контексте. Видеозапись добровольна.",
        active=True,
    ),
    dict(
        key="hads",
        name="HADS",
        description="Госпитальная шкала тревоги и депрессии. Выбранная русская форма в переданных PDF отсутствует.",
        active=False,
    ),
    dict(
        key="madrs",
        name="MADRS",
        description="Врачебная оценка: нужны точная русская форма и полные клинические опорные описания.",
        active=False,
    ),
    dict(
        key="hdrs17",
        name="HDRS-17",
        description="Врачебная оценка: нужны критерии выбранной версии с диапазонами всех 17 пунктов.",
        active=False,
    ),
]
ASSIGNABLE = set(SCALES) | {"phqads"}
MEDICINES = [
    {
        "slug": "aviandr",
        "name": "Авиандр",
        "description": "Информация из предоставленного листка-вкладыша и исходный PDF.",
    }
]
