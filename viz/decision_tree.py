"""Decision tree — live classification from DataProfile + data-to-viz reference.

Wires decesion tree/DATA_TO_VIZ_SELECTION_REFERENCE.md Input Classification + Core Decision Tree
+ Choosing Between Candidates + General Quality Rules as candidate generator.

Uses profiler live DataProfile (efficient, no LLM re-inference per Q3) and VizSpec intent/dimensions/metrics
to return compatible chart candidates before scoring.

Reference: DATA_TO_VIZ_SELECTION_REFERENCE.md §1-6
- Input Classification: Data family, Numeric fields, Categorical fields, Ordered, Sample size ~2000, Group structure etc.
- Core Decision Tree: Numeric / Categorical / Mixed / Time-series / Map / Network branches
- Choosing Between Candidates: ranking→bar/lollipop, evolution→line, proportion→bars, etc.
- General Quality: ordering, overplotting, colour, etc. (applied as warnings, not hard gates)
"""

from __future__ import annotations

from viz.profiler import DataProfile


# Input Classification helper
def classify_data_profile(dp: DataProfile, vizspec: dict | None = None) -> dict:
    """Return classification dict from live profiler DataProfile.

    Fields: data_family, numeric_fields, categorical_fields, ordered, sample_size,
            group_structure, series_count, analytical_goal (from vizspec intent)
    """
    n_num = len(dp.numeric_cols)
    n_cat = len(dp.categorical_cols)
    n_temp = len(dp.temporal_cols)
    has_temporal = n_temp > 0
    # Ordered if temporal exists or vizspec time_bucket != none
    time_bucket = (vizspec or {}).get("time_bucket", "none") if vizspec else "none"
    ordered = has_temporal or time_bucket != "none"
    sample_size = dp.row_count
    many = sample_size > 2000  # threshold from reference
    # Group structure: check if any categorical has many observations per group (cardinality < row_count/2)
    group_several = False
    for c in dp.columns:
        if c.role in ("categorical", "temporal") and c.cardinality < dp.row_count / 2:
            group_several = True
            break
    # Series count: if vizspec metrics >1 or dimensions with group_by => several series
    metrics = (vizspec or {}).get("metrics", []) if vizspec else []
    dimensions = (vizspec or {}).get("dimensions", []) if vizspec else []
    series_several = len(metrics) > 1 or len(dimensions) > 1
    # Data family
    if has_temporal:
        data_family = "time_series" if n_num >= 1 else "temporal"
    elif n_num >= 1 and n_cat >= 1:
        data_family = "mixed"
    elif n_num >= 1 and n_cat == 0:
        data_family = "numeric"
    elif n_cat >= 1 and n_num == 0:
        data_family = "categorical"
    else:
        data_family = "other"

    return {
        "data_family": data_family,
        "numeric_fields": n_num,
        "categorical_fields": n_cat,
        "temporal_fields": n_temp,
        "ordered": ordered,
        "sample_size": sample_size,
        "many_points": many,
        "group_several": group_several,
        "series_count": "several" if series_several else "one",
        "time_bucket": time_bucket,
    }


def candidate_charts_for_classification(cls: dict, vizspec: dict | None = None) -> list[str]:
    """Return candidate chart families from Core Decision Tree branches."""
    family = cls["data_family"]
    n_num = cls["numeric_fields"]
    n_cat = cls["categorical_fields"]
    ordered = cls["ordered"]
    many = cls["many_points"]
    group_several = cls["group_several"]
    series = cls["series_count"]
    time_bucket = cls["time_bucket"]
    intent = ((vizspec or {}).get("intent") or ["comparison"])
    intent_primary = intent[0] if isinstance(intent, list) and intent else "comparison"
    # Ranking without a trend component is a bar-family question even when the
    # dataset has a temporal column ("top 10 customers by revenue" must not
    # become a line chart just because DATE exists).
    if intent_primary == "ranking" and "trend" not in intent and time_bucket == "none":
        return ["horizontal_bar", "bar", "lollipop"]
    # Normalize time_bucket intent: trend over time → line
    if time_bucket != "none" or intent_primary == "trend" or "trend" in intent:
        if series == "several":
            return ["line", "stacked_area", "streamgraph"]
        return ["line", "area", "bar"]
    # Data-to-viz decision tree branches (simplified implementation)
    if family == "numeric" and n_num == 1:
        return ["histogram", "density"]
    if family == "numeric" and n_num == 2 and ordered:
        return ["line", "area", "connected_scatter"]
    if family == "numeric" and n_num == 2 and not ordered:
        return ["scatter", "boxplot", "histogram"] if not many else ["violin", "density", "scatter"]
    if family == "numeric" and n_num >= 3 and ordered:
        return ["stacked_area", "line", "streamgraph"]
    if family == "numeric" and n_num >= 3 and not ordered:
        return ["boxplot", "heatmap", "correlogram"] if n_num >= 3 else ["scatter"]
    if family == "categorical" and n_cat == 1:
        # One categorical → bar/lollipop/pie family (prefer bar for ranking, pie cautious)
        if intent_primary in ("composition",):
            return ["pie", "bar", "lollipop"]
        return ["bar", "lollipop", "horizontal_bar"]
    if family == "categorical" and n_cat >= 2:
        # Two+ categoricals → depends on hierarchy/subgroups/adjacency; generic
        return ["grouped_bar", "stacked_bar", "heatmap"]
    if family == "mixed":
        # One numeric + one categorical
        if intent_primary in ("distribution",):
            return ["boxplot", "violin", "histogram"] if group_several else ["bar", "lollipop"]
        if intent_primary in ("trend",):
            return ["line", "stacked_area"]
        if intent_primary in ("composition",):
            return ["stacked_bar", "pie", "bar"]
        if intent_primary in ("relationship", "correlation"):
            return ["scatter", "heatmap"]
        # Default mixed: bar vs grouped
        if len((vizspec or {}).get("dimensions", [])) >= 2 or len((vizspec or {}).get("metrics", [])) > 1:
            return ["grouped_bar", "stacked_bar", "bar"]
        return ["bar", "horizontal_bar", "lollipop"]
    if family == "time_series":
        if series == "several":
            return ["line", "stacked_area", "heatmap"]
        return ["line", "area", "bar"]
    # Fallback
    return ["bar", "line", "scatter"]


def decision_tree_candidates(dp: DataProfile, vizspec: dict | None = None) -> list[str]:
    """Public entry: live classification → candidate chart types."""
    cls = classify_data_profile(dp, vizspec)
    cands = candidate_charts_for_classification(cls, vizspec)
    # Apply Choosing Between Candidates preferences (ranking→bar, evolution→line already in mapping)
    # Ensure intent_primary boosts its family already handled above; add small refinement:
    intent = ((vizspec or {}).get("intent") or ["comparison"])
    if "ranking" in intent and "bar" not in cands and "horizontal_bar" not in cands:
        cands = ["horizontal_bar", "bar"] + cands
    if "composition" in intent and "pie" not in cands and "stacked_bar" not in cands:
        cands = ["pie", "stacked_bar"] + cands
    # Deduplicate preserve order, cap 5
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= 5:
            break
    return out
