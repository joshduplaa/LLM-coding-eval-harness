"""Generate deploy/k8s/grafana/evalharness.json (run: uv run python scripts/gen_dashboard.py).

Rules the dashboard follows (D15, D21): a pass rate is never shown without its 95% interval and
sample size, harness errors are shown next to it, and mock data is labeled on every panel.
"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
SEL = 'model_id=~"$model"'
_id = iter(range(1, 100))


def q(expr, ref, legend="", instant=False, fmt="time_series"):
    return {
        "datasource": DS,
        "expr": expr,
        "refId": ref,
        "legendFormat": legend,
        "instant": instant,
        "range": not instant,
        "format": fmt,
    }


def panel(kind, title, x, y, w, h, targets=(), **extra):
    return {
        "id": next(_id),
        "type": kind,
        "title": title,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": list(targets),
        **extra,
    }


BANNER = (
    "## ⚠ MOCK DATA: synthetic output, not a measurement of any real model\n"
    "Every series carries `is_mock`. Until real model calls are approved, every model here is a "
    "**mock** that returns canned solutions. Rates are shown with their 95% Wilson interval and "
    "sample size `n`; harness errors are excluded from `n` and counted separately. Samples of "
    "the same task are correlated, so the true uncertainty is wider than shown. "
    "Nothing here compares models."
)


def rate_table(title, y, task_filter, by):
    sel = f"{SEL},{task_filter}"
    by_labels = ", ".join(by + ["is_mock"])

    def row(metric, ref):
        # Aggregating drops __name__/instance/job, so the five queries share the same labels
        # and the "merge" transformation can join them into one row per model (and task).
        return q(f"max by ({by_labels}) ({metric}{{{sel}}})", ref, instant=True, fmt="table")

    targets = [
        row("evalharness_pass_rate", "rate"),
        row("evalharness_pass_rate_ci_low", "low"),
        row("evalharness_pass_rate_ci_high", "high"),
        row("evalharness_samples_scored", "n"),
        row("evalharness_samples_excluded", "excluded"),
    ]
    rename = {
        "Value #rate": "pass rate",
        "Value #low": "95% CI low",
        "Value #high": "95% CI high",
        "Value #n": "n (scored)",
        "Value #excluded": "harness errors (excluded)",
    }
    exclude = {"Time": True, "__name__": True, "instance": True, "job": True}
    if "task" not in by:
        exclude["task"] = True
    pct = {"unit": "percentunit", "decimals": 1, "min": 0, "max": 1}
    return panel(
        "table",
        title,
        0,
        y,
        24,
        7,
        targets,
        transformations=[
            {"id": "merge", "options": {}},
            {
                "id": "organize",
                "options": {"excludeByName": exclude, "renameByName": rename, "indexByName": {}},
            },
        ],
        fieldConfig={
            "defaults": {},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": n},
                    "properties": [
                        {"id": "unit", "value": pct["unit"]},
                        {"id": "decimals", "value": 1},
                    ],
                }
                for n in ("pass rate", "95% CI low", "95% CI high")
            ]
            + [
                {
                    "matcher": {"id": "byName", "options": "is_mock"},
                    "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "true": {"text": "MOCK", "color": "red"},
                                        "false": {"text": "real", "color": "green"},
                                    },
                                }
                            ],
                        },
                    ],
                },
            ],
        },
        options={"showHeader": True, "cellHeight": "sm"},
    )


panels = [
    panel("text", "", 0, 0, 16, 5, options={"mode": "markdown", "content": BANNER}),
    panel(
        "stat",
        "Mock models shown",
        16,
        0,
        4,
        5,
        [
            q(
                'count(evalharness_samples_scored{task="_all",is_mock="true"}) or vector(0)',
                "A",
                instant=True,
            )
        ],
        fieldConfig={
            "defaults": {
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}],
                },
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        options={"colorMode": "background", "reduceOptions": {"calcs": ["lastNotNull"]}},
    ),
    panel(
        "stat",
        "Runs stored",
        20,
        0,
        4,
        5,
        [q("evalharness_runs_stored", "A", instant=True)],
        options={"reduceOptions": {"calcs": ["lastNotNull"]}},
    ),
    rate_table(
        "Latest run per model: pass rate, 95% CI, sample size", 5, 'task="_all"', ["model_id"]
    ),
    rate_table("Latest run per model, by task", 12, 'task!="_all"', ["model_id", "task"]),
    panel(
        "bargauge",
        "Samples by result category (latest run, all tasks)",
        0,
        19,
        12,
        9,
        [
            q(
                f'evalharness_samples{{{SEL},task="_all"}}',
                "A",
                "{{model_id}} · {{category}}",
                instant=True,
            )
        ],
        options={
            "orientation": "horizontal",
            "displayMode": "basic",
            "showUnfilled": True,
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
        # Only "passed" is green: a wrong answer or timeout must not look like a good result.
        fieldConfig={
            "defaults": {
                "min": 0,
                "decimals": 0,
                "color": {"mode": "fixed", "fixedColor": "#8AB8FF"},
            },
            "overrides": [
                {
                    "matcher": {"id": "byRegexp", "options": f".*· {cat}$"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}],
                }
                for cat, c in [
                    ("passed", "green"),
                    ("sandbox_violation", "red"),
                    ("harness_error", "orange"),
                ]
            ],
        },
    ),
    panel(
        "timeseries",
        "Overall pass rate over time, with 95% CI band",
        12,
        19,
        12,
        9,
        [
            q(
                f'evalharness_pass_rate_ci_high{{{SEL},task="_all"}}',
                "high",
                "{{model_id}} CI high",
            ),
            q(f'evalharness_pass_rate{{{SEL},task="_all"}}', "rate", "{{model_id}} pass rate"),
            q(f'evalharness_pass_rate_ci_low{{{SEL},task="_all"}}', "low", "{{model_id}} CI low"),
        ],
        description="The value changes only when a newer run of a model is stored.",
        fieldConfig={
            "defaults": {
                "unit": "percentunit",
                "min": 0,
                "max": 1,
                "custom": {"lineWidth": 1, "fillOpacity": 0},
            },
            "overrides": [
                {
                    "matcher": {"id": "byRegexp", "options": ".*CI high"},
                    "properties": [
                        {"id": "custom.fillBelowTo", "value": "${__field.labels.model_id} CI low"},
                        {"id": "custom.fillOpacity", "value": 15},
                        {"id": "custom.lineWidth", "value": 0},
                    ],
                },
                {
                    "matcher": {"id": "byRegexp", "options": ".*CI low"},
                    "properties": [{"id": "custom.lineWidth", "value": 0}],
                },
                {
                    "matcher": {"id": "byRegexp", "options": ".*pass rate"},
                    "properties": [{"id": "custom.lineWidth", "value": 2}],
                },
            ],
        },
    ),
]
dashboard = {
    "uid": "evalharness",
    "title": "evalharness: pass rates (MOCK DATA)",
    "tags": ["evalharness", "mock"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": False,
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {
        "list": [
            {
                "name": "model",
                "label": "model",
                "type": "query",
                "datasource": DS,
                "query": {
                    "query": "label_values(evalharness_samples_scored, model_id)",
                    "refId": "m",
                },
                "refresh": 2,
                "multi": True,
                "includeAll": True,
                "allValue": ".*",
                "current": {"text": "All", "value": "$__all"},
            }
        ]
    },
    "panels": panels,
}
out = Path(__file__).resolve().parents[1] / "deploy/k8s/grafana/evalharness.json"
out.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
print(out)
