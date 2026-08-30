"""Scoring — explainable suitability 0-100 per candidate."""

from __future__ import annotations

from schemas import ChartSpec
from viz.profiler import DataProfile

def score_candidate(spec: ChartSpec, profile: DataProfile, intent_goal: str, explicit_requested: bool = False) -> tuple[float, str, dict]:
    """Return (score, reason, breakdown)."""
    x = profile.by_name(spec.x) if spec.x else None
    y = profile.by_name(spec.y) if spec.y else None
    g = profile.by_name(spec.group_by) if spec.group_by else None

    breakdown: dict[str, float] = {}
    reasons: list[str] = []

    # Base fit
    data_fit = 50.0
    if spec.chart_type in {"bar", "horizontal_bar"}:
        if x and x.role == "categorical":
            data_fit = 80
            reasons.append("categorical x fits bar")
        elif x and x.role == "temporal":
            data_fit = 30
            reasons.append("temporal x less ideal for bar (line better)")
        if x and 3 <= x.cardinality <= 15:
            data_fit += 10
        # long labels boost h_bar
        if x and x.avg_label_len and x.avg_label_len > 12 and spec.chart_type == "horizontal_bar":
            data_fit += 10
            reasons.append("long labels favor horizontal")
        if x and x.avg_label_len and x.avg_label_len > 12 and spec.chart_type == "bar":
            data_fit -= 10

    elif spec.chart_type in {"grouped_bar", "stacked_bar", "stacked_100"}:
        data_fit = 75
        if x and g:
            if 2 <= x.cardinality <= 10 and 2 <= g.cardinality <= 6:
                data_fit = 85
                reasons.append("balanced x and group sizes for grouped/stacked")
            if x.cardinality * g.cardinality > 30:
                data_fit -= 15

    elif spec.chart_type in {"line", "area"}:
        if x and x.role == "temporal":
            data_fit = 90
            reasons.append("temporal x ideal for trend")
        elif x and x.ordered:
            data_fit = 70
        else:
            data_fit = 50
        if y and not y.variance_zero:
            data_fit += 5

    elif spec.chart_type == "scatter":
        data_fit = 80 if (x and y and x.role == "numeric" and y.role == "numeric") else 30
        if profile.row_count >= 50:
            data_fit += 5

    elif spec.chart_type == "histogram":
        data_fit = 85 if (x or y) and (x or y).role == "numeric" else 30  # type: ignore
        if (x or y) and (x or y).cardinality >= 10:  # type: ignore
            data_fit += 5

    elif spec.chart_type == "boxplot":
        data_fit = 80
        if y and y.variance_zero:
            data_fit = 30

    elif spec.chart_type == "heatmap":
        data_fit = 80 if len(profile.numeric_cols) >= 3 else 40
        if intent_goal == "correlation":
            data_fit += 10

    elif spec.chart_type in {"pie", "donut"}:
        if x and 2 <= x.cardinality <= 5:
            data_fit = 75
            reasons.append("2-5 categories ideal for pie/donut")
        elif x and x.cardinality == 6:
            data_fit = 60
        elif x and x.cardinality > 6:
            data_fit = 30
            reasons.append("many categories penalize pie")
        if y and y.has_negatives:
            data_fit = 20
        if intent_goal == "composition":
            data_fit += 10
            reasons.append("composition goal aligns with pie")

    breakdown["data_fit"] = data_fit

    # Goal alignment
    goal_alignment = 10
    goal_map = {
        "comparison": {"bar", "horizontal_bar", "grouped_bar"},
        "ranking": {"horizontal_bar", "bar"},
        "trend": {"line", "area"},
        "distribution": {"histogram", "boxplot"},
        "relationship": {"scatter"},
        "correlation": {"heatmap", "scatter"},
        "composition": {"pie", "donut", "stacked_bar", "stacked_100"},
        "overview": {"bar", "line", "histogram", "scatter", "heatmap"},
    }
    if spec.chart_type in goal_map.get(intent_goal, set()):
        goal_alignment = 25
        reasons.append(f"goal '{intent_goal}' matches {spec.chart_type}")
    elif intent_goal == "overview":
        goal_alignment = 15
    breakdown["goal_alignment"] = goal_alignment

    # Cardinality fit
    card_fit = 10
    if x:
        if spec.chart_type in {"bar", "horizontal_bar"} and 2 <= x.cardinality <= 20:
            card_fit = 15
        elif spec.chart_type in {"pie", "donut"} and 2 <= x.cardinality <= 6:
            card_fit = 15
        elif spec.chart_type == "histogram" and x.cardinality >= 8:
            card_fit = 15
    breakdown["cardinality_fit"] = card_fit

    # Clarity (penalties for high cardinality without top-N, etc.)
    clarity = 10
    if x and x.cardinality > 30 and "top" not in (spec.data_notes or "").lower():
        clarity -= 5
        reasons.append("high cardinality without top-N reduces clarity")
    breakdown["clarity"] = clarity

    # Explicit request boost
    if explicit_requested:
        data_fit += 12
        reasons.append("explicit user request boosts score")

    # Null rate penalty
    if x and x.null_rate > 0.3:
        data_fit -= 15
        reasons.append("high null rate penalized")

    score = data_fit * 0.5 + goal_alignment * 1.5 + card_fit * 1.0 + clarity * 1.0
    # Normalize to 0-100
    score = max(0, min(100, score))

    reason_str = "; ".join(reasons) if reasons else f"{intent_goal} candidate"
    return score, reason_str, breakdown
