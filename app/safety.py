"""Teaching adaptation from the supplied article, NOT an official C-SSRS form.

Question wording is copied from the user's pasted text. Branches and periods follow
Plan.pdf pp.4,15 and SpecificationV2 pp.14-16. No risk score, risk category,
prediction, treatment or admission decision is produced. Explicit answers may
trigger a separate request for prompt clinician contact, without a risk score.
"""

from copy import deepcopy

SAFETY_VERSION = "supplied-cssrs-teaching-2026-10-18-v1"
SAFETY_ITEMS = [
    "За последний месяц вы когда-нибудь желали, чтобы вас не стало? Или чтобы вы заснули и не проснулись?",
    "А у вас были мысли о том, чтобы убить себя?",
    "Вы думали о том, как бы вы это сделали?",
    "У вас были эти мысли, и было ли у вас какое-то намерение действовать по ним?",
    "Вы начали разрабатывать или уже разработали детали того, как покончить с собой? Вы намеревались осуществить этот план?",
    "Вы когда-нибудь делали что-то, начинали что-то делать или готовились что-то сделать, чтобы покончить с жизнью?",
    "Это было за последние 3 месяца?",
]
SAFETY_EXPLANATIONS = [
    "Вопрос касается желания не жить, даже если нет намерения что-либо делать. Можно рассказать об этом своими словами отдельно от ответа.",
    "Это прямой вопрос о мыслях. Дополнительный рассказ можно оставить в комментарии или обсудить лично.",
    "Вопрос уточняет содержание мыслей. Подробности можно обсудить со специалистом; перечислять их в приложении не требуется.",
    "Мысли и намерение действовать — разные сведения. Можно добавить комментарий, если одного ответа недостаточно.",
    "В вопросе затрагиваются план и намерение. Если ответ на его части различается, поясните это в комментарии и обсудите со специалистом.",
    "Здесь спрашивается о поведении за жизнь, отдельно от мыслей. Вы вправе не раскрывать подробности в приложении.",
    "Уточняется время события. Если трудно вспомнить, можно отложить ответ и пояснить это специалисту.",
]


def answered_value(answers, key):
    answer = answers.get(str(key), {})
    return answer.get("value") if answer.get("state") == "answered" else None


def safety_path(answers):
    path = [1, 2]
    if answered_value(answers, 2) == 1:
        path.extend([3, 4, 5])
    path.append(6)
    if answered_value(answers, 6) == 1:
        path.append(7)
    return path


def normalize_safety(answers):
    """A missing Q2 never becomes no; invalidated branch values go to history.

    Keep the patient's narrative and discuss flag even when a structured value
    no longer belongs to the current branch.
    """
    result = deepcopy(answers)
    hidden = set()
    if answered_value(result, 2) == 0:
        hidden.update(["3", "4", "5"])
    if answered_value(result, 6) == 0:
        hidden.add("7")
    for i in range(1, 8):
        key = str(i)
        answer = result.setdefault(
            key, dict(value=None, state="unanswered", comment="", discuss=0)
        )
        if key in hidden:
            answer.update(value=None, state="not_asked_by_rule")
        elif answer.get("state") == "not_asked_by_rule":
            answer.update(value=None, state="unanswered")
    return result


def safety_result(answers):
    for key in map(str, range(1, 8)):
        value = answered_value(answers, key)
        if value is not None and (type(value) is not int or value not in (0, 1)):
            raise ValueError("Недопустимый ответ")
    data = normalize_safety(answers)
    required = [1, 2, 6]
    if answered_value(data, 2) != 0:
        required.extend([3, 4, 5])
    if answered_value(data, 6) != 0:
        required.append(7)
    count = sum(answered_value(data, i) is not None for i in required)
    return dict(
        total=None,
        answered=count,
        count=len(required),
        complete=count == len(required),
        positive_items=[
            str(i) for i in safety_path(data) if answered_value(data, i) == 1
        ],
        urgent=bool(safety_urgent_items(data)),
        urgent_items=safety_urgent_items(data),
    )


def safety_urgent_items(answers):
    """Contact triggers from answers, never a sum or a diagnostic risk score."""
    result = []
    if answered_value(answers, 2) == 1:
        result.extend(str(i) for i in (4, 5) if answered_value(answers, i) == 1)
    if answered_value(answers, 6) == 1 and answered_value(answers, 7) == 1:
        result.append("7")
    return result
