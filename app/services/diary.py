"""Additive diary measurements. Definitions belong to the leading doctor."""

from decimal import Decimal, InvalidOperation
from fastapi import HTTPException


def number(value, label):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise HTTPException(422, f"{label}: введите число")
    if not result.is_finite() or abs(result) > Decimal('1e12'):
        raise HTTPException(422, f"{label}: недопустимое число")
    return result


def sleep_rating(raw):
    if raw is None or not str(raw).strip():
        return None
    value = number(raw, 'Качество сна')
    if value != value.to_integral_value() or not 0 <= value <= 10:
        raise HTTPException(422, 'Качество сна: выберите целое число от 0 до 10')
    return int(value)


def metric_definition(form):
    title = str(form.get('title', '')).strip()
    unit = str(form.get('unit', '')).strip()
    kind = str(form.get('kind', ''))
    if not 1 <= len(title) <= 100 or len(unit) > 24 or kind not in ('scale', 'number', 'boolean', 'text'):
        raise HTTPException(422, 'Проверьте название, формат и единицу показателя')
    minimum = maximum = None
    if kind == 'scale':
        minimum, maximum = 0, 10
    elif kind == 'number':
        minimum = number(form['minimum'], 'Минимум') if form.get('minimum') else None
        maximum = number(form['maximum'], 'Максимум') if form.get('maximum') else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise HTTPException(422, 'Минимум не может быть больше максимума')
    elif unit:
        raise HTTPException(422, 'Единица нужна только для числового показателя')
    return title, kind, unit, float(minimum) if minimum is not None else None, float(maximum) if maximum is not None else None


def read_diary_extras(c, pid):
    metrics = [dict(r) for r in c.execute('SELECT * FROM diary_metrics WHERE patient_id=? ORDER BY id', (pid,))]
    history, latest = {}, {}
    for row in c.execute(
        '''SELECT v.*,m.title,m.kind,m.unit,d.day FROM diary_metric_values v
           JOIN diary d ON d.id=v.diary_id JOIN diary_metrics m ON m.id=v.metric_id
           WHERE d.patient_id=? AND m.patient_id=? ORDER BY d.day,d.id''', (pid, pid),
    ):
        item = dict(row)
        item['display'] = {'true': 'Да', 'false': 'Нет'}.get(item['value'], item['value']) if item['kind'] == 'boolean' else item['value']
        history.setdefault(row['diary_id'], []).append(item)
        latest[row['metric_id']] = item
    return dict(diary_metrics=metrics, diary_metric_history=history, diary_metric_latest=latest)


def parse_metric_values(c, pid, form):
    definitions = {str(r['id']): dict(r) for r in c.execute('SELECT * FROM diary_metrics WHERE patient_id=?', (pid,))}
    values = []
    for key in form:
        if not key.startswith('metric_'):
            continue
        metric = definitions.get(key[7:])
        if not metric or len(form.getlist(key)) != 1:
            raise HTTPException(422, 'Этот показатель не назначен данному пациенту')
        value = str(form[key]).strip()
        if not value:
            continue
        if metric['kind'] in ('scale', 'number'):
            result = number(value, metric['title'])
            if (metric['minimum'] is not None and result < Decimal(str(metric['minimum']))) or (metric['maximum'] is not None and result > Decimal(str(metric['maximum']))):
                raise HTTPException(422, f"{metric['title']}: значение вне назначенного диапазона")
            if metric['kind'] == 'scale' and result != result.to_integral_value():
                raise HTTPException(422, 'Для шкалы 0–10 выберите целое число')
            value = str(result)
        elif metric['kind'] == 'boolean' and value not in ('true', 'false'):
            raise HTTPException(422, 'Выберите «Да» или «Нет»')
        elif metric['kind'] == 'text' and len(value) > 1000:
            raise HTTPException(422, 'Ответ не должен быть длиннее 1000 символов')
        values.append((metric['id'], value))
    return values
