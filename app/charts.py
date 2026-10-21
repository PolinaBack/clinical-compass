"""Calendar-based chart coordinates shared by self-reports and diary charts."""

from datetime import date, timedelta


def chart(rows, maximum, name, scale, date_key="period_end", value_key="total"):
    rows = sorted(rows, key=lambda r: (r[date_key], r["id"]))
    dates = [date.fromisoformat(r[date_key]) for r in rows]
    first = min(dates) if dates else date.today()
    last = max(dates) if dates else first
    span = max((last - first).days, 1)
    points = []
    segments = []
    current = []
    for row in rows:
        value = row[value_key]
        if value is None:
            if current:
                segments.append(" ".join(current))
                current = []
            continue
        x = (
            85 + (date.fromisoformat(row[date_key]) - first).days * 510 / span
            if first != last
            else 340
        )
        y = 200 - value / maximum * 150
        current.append(f"{x:.1f},{y:.1f}")
        points.append(dict(x=x, y=y, total=value, assessment=row, date=row[date_key]))
    if current:
        segments.append(" ".join(current))
    tick_dates = (
        [first]
        if first == last
        else sorted(
            {
                first + timedelta(days=round((last - first).days * i / 3))
                for i in range(4)
            }
        )
    )
    xticks = [
        dict(
            x=340 if first == last else 85 + (d - first).days * 510 / span,
            label=d.strftime("%d.%m.%Y"),
        )
        for d in tick_dates
    ]
    yvalues = sorted({0, maximum, *range(5, maximum, 5)})
    return dict(
        name=name,
        scale=scale,
        max=maximum,
        points=points,
        segments=segments,
        xticks=xticks,
        yticks=[dict(y=200 - v / maximum * 150, label=v) for v in yvalues],
        y_label="Самочувствие, 0–10" if scale == "diary" else f"Баллы {name}",
        x_label="Дата записи" if scale == "diary" else "Дата оценки",
    )
