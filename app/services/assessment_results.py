"""Shared graph and paired-scale context for both result views."""

from app.charts import chart
from app.scales import SCALES, combined


def results_context(overview):
    data = dict(
        overview,
        assessments=[a for a in overview["assessments"] if a["status"] == "completed"],
    )
    pairs = {}
    for a in data["assessments"]:
        if a["scale"] != "safety6":
            pairs.setdefault(a["session_id"], {})[a["scale"]] = dict(
                a, total=a["result"]["total"]
            )
    summaries = []
    for sid, forms in pairs.items():
        total, reason = combined(forms.get("phq9"), forms.get("gad7"))
        if total is not None:
            summaries.append(dict(sid=sid, total=total, reason=reason))
    graphs = []
    for scale in ["phq4", "phq9", "gad7", "hcl33"]:
        rows = [
            dict(a, total=a["result"]["total"])
            for a in data["assessments"]
            if a["scale"] == scale
        ]
        if rows:
            graphs.append(
                chart(rows, SCALES[scale]["max"], SCALES[scale]["name"], scale)
            )
    return dict(data=data, graphs=graphs, summaries=summaries)
