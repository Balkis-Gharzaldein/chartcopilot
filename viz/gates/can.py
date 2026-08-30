"""CAN gates — technical feasibility per chart family."""

from __future__ import annotations

from schemas import ChartSpec
from viz.profiler import DataProfile
from viz.gates.base import GateResult

def can_gate(spec: ChartSpec, profile: DataProfile) -> GateResult:
    """Check if chart can technically be generated."""
    ctype = spec.chart_type
    n_obs = profile.row_count

    def col(role: str | None = None):
        # helper to get column meta quickly
        pass

    # Common: need x
    if ctype in {"bar", "horizontal_bar", "pie", "donut", "histogram", "boxplot"}:
        # x required for categorical, optional for histogram/box
        pass

    # Dispatch per family
    if ctype in {"bar", "horizontal_bar"}:
        return _can_bar(spec, profile)
    if ctype in {"grouped_bar", "stacked_bar", "stacked_100"}:
        return _can_grouped_stacked(spec, profile)
    if ctype in {"line", "area"}:
        return _can_line(spec, profile)
    if ctype == "scatter":
        return _can_scatter(spec, profile)
    if ctype == "histogram":
        return _can_histogram(spec, profile)
    if ctype == "boxplot":
        return _can_boxplot(spec, profile)
    if ctype == "heatmap":
        return _can_heatmap(spec, profile)
    if ctype in {"pie", "donut"}:
        return _can_pie(spec, profile)
    return GateResult(passed=False, reason=f"Unknown chart type {ctype}", gate="CAN")

def _get_meta(profile: DataProfile, name: str | None):
    if not name: return None
    return profile.by_name(name)

def _can_bar(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get_meta(p, spec.x)
    y = _get_meta(p, spec.y)
    if not spec.x or not x:
        return GateResult(False, "Bar requires categorical x", "CAN")
    if x.role not in {"categorical", "temporal"}:
        # allow numeric with low cardinality as categorical
        if x.role == "numeric" and x.cardinality > 30:
            return GateResult(False, f"x '{spec.x}' is high-cardinality numeric, not suitable for bar", "CAN")
    if not spec.y and spec.agg_function not in {"count", "count_distinct"}:
        # count is allowed without y
        if not y:
            return GateResult(False, "Bar requires numeric y or count aggregation", "CAN")
    if p.row_count < 2:
        return GateResult(False, "Need ≥2 rows for bar", "CAN")
    return GateResult(True, "CAN: bar feasible", "CAN")

def _can_grouped_stacked(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get_meta(p, spec.x)
    y = _get_meta(p, spec.y)
    g = _get_meta(p, spec.group_by)
    if not spec.x or not x:
        return GateResult(False, "Grouped/stacked requires x", "CAN")
    if not spec.group_by or not g:
        return GateResult(False, "Grouped/stacked requires group_by categorical", "CAN")
    if g.role not in {"categorical", "temporal"} and g.cardinality > 20:
        return GateResult(False, f"group_by '{spec.group_by}' has too many categories", "CAN")
    if g.cardinality < 2 or g.cardinality > 12:
        return GateResult(False, f"group_by cardinality {g.cardinality} not in 2..12 for grouped/stacked", "CAN")
    if x.cardinality < 2:
        return GateResult(False, "x needs ≥2 categories for grouped", "CAN")
    if not y and spec.agg_function not in {"count", "count_distinct"}:
        return GateResult(False, "Grouped/stacked needs numeric y or count", "CAN")
    return GateResult(True, "CAN: grouped/stacked feasible", "CAN")

def _can_line(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get_meta(p, spec.x)
    y = _get_meta(p, spec.y)
    if not spec.x or not x:
        return GateResult(False, "Line requires x", "CAN")
    if x.role not in {"temporal"} and not x.ordered:
        # allow categorical if ordered or low cardinality? Strict: need temporal or ordered
        # For CAN we allow categorical but APPROPRIATE will downgrade
        pass
    if not spec.y or not y:
        return GateResult(False, "Line requires numeric y", "CAN")
    if y.role != "numeric":
        return GateResult(False, f"y '{spec.y}' must be numeric for line", "CAN")
    if x.cardinality < 2:
        return GateResult(False, "Line needs ≥2 distinct x values", "CAN")
    if p.row_count < 3:
        return GateResult(False, "Line needs ≥3 rows", "CAN")
    return GateResult(True, "CAN: line/area feasible", "CAN")

def _can_scatter(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get_meta(p, spec.x)
    y = _get_meta(p, spec.y)
    if not spec.x or not spec.y or not x or not y:
        return GateResult(False, "Scatter requires x and y", "CAN")
    if x.role != "numeric" or y.role != "numeric":
        return GateResult(False, "Scatter requires both axes numeric", "CAN")
    if p.row_count < 10:
        return GateResult(False, f"Scatter needs ≥10 rows, have {p.row_count}", "CAN")
    return GateResult(True, "CAN: scatter feasible", "CAN")

def _can_histogram(spec: ChartSpec, p: DataProfile) -> GateResult:
    # histogram: x is numeric (or y), no group_by required
    col_name = spec.x or spec.y
    m = _get_meta(p, col_name)
    if not col_name or not m:
        return GateResult(False, "Histogram requires numeric column", "CAN")
    if m.role != "numeric":
        return GateResult(False, f"Histogram needs numeric, got {m.role}", "CAN")
    if p.row_count < 10:
        return GateResult(False, "Histogram needs ≥10 rows", "CAN")
    return GateResult(True, "CAN: histogram feasible", "CAN")

def _can_boxplot(spec: ChartSpec, p: DataProfile) -> GateResult:
    # box: y numeric, x optional categorical group
    y = _get_meta(p, spec.y) or _get_meta(p, spec.x)
    if not y or y.role != "numeric":
        return GateResult(False, "Box plot requires numeric measure", "CAN")
    if spec.x:
        xm = _get_meta(p, spec.x)
        if xm and xm.role == "identifier":
            return GateResult(False, "Box x cannot be identifier", "CAN")
    if p.row_count < 10:
        return GateResult(False, "Box needs ≥10 rows", "CAN")
    return GateResult(True, "CAN: box feasible", "CAN")

def _can_heatmap(spec: ChartSpec, p: DataProfile) -> GateResult:
    # correlation heatmap: need ≥3 numerics, or 2 categoricals? For now correlation matrix
    n_num = len(p.numeric_cols)
    if n_num < 3:
        return GateResult(False, f"Heatmap (correlation) needs ≥3 numeric cols, have {n_num}", "CAN")
    if p.row_count < 20:
        return GateResult(False, "Heatmap needs ≥20 rows", "CAN")
    return GateResult(True, "CAN: heatmap feasible", "CAN")

def _can_pie(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get_meta(p, spec.x)
    if not spec.x or not x:
        return GateResult(False, "Pie/donut requires categorical x", "CAN")
    if x.role == "identifier":
        return GateResult(False, "Pie x cannot be identifier", "CAN")
    if x.cardinality < 2:
        return GateResult(False, "Pie needs ≥2 categories", "CAN")
    if x.cardinality > 30:
        # Still feasible via bucketing to top 10 + other, but will be penalized in scoring
        return GateResult(True, f"CAN: pie with {x.cardinality} cats feasible via bucketing", "CAN")
    if p.row_count < 2:
        return GateResult(False, "Pie needs ≥2 rows", "CAN")
    return GateResult(True, "CAN: pie feasible", "CAN")
