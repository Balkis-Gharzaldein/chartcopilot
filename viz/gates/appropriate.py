"""APPROPRIATE gates — statistical/visual appropriateness."""

from __future__ import annotations

from schemas import ChartSpec
from viz.gates.base import GateResult
from viz.profiler import DataProfile

def appropriate_gate(spec: ChartSpec, profile: DataProfile) -> GateResult:
    ctype = spec.chart_type
    if ctype in {"bar", "horizontal_bar"}:
        return _app_bar(spec, profile)
    if ctype in {"grouped_bar", "stacked_bar", "stacked_100"}:
        return _app_grouped(spec, profile)
    if ctype in {"line", "area"}:
        return _app_line(spec, profile)
    if ctype == "scatter":
        return _app_scatter(spec, profile)
    if ctype == "histogram":
        return _app_histogram(spec, profile)
    if ctype == "boxplot":
        return _app_boxplot(spec, profile)
    if ctype == "heatmap":
        return _app_heatmap(spec, profile)
    if ctype in {"pie", "donut"}:
        return _app_pie(spec, profile)
    return GateResult(True, "APPROPRIATE: unknown type, assume ok", "APPROPRIATE")

def _get(p: DataProfile, name: str | None):
    return p.by_name(name) if name else None

def _app_bar(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get(p, spec.x)
    if not x: return GateResult(False, "Missing x for bar appropriateness", "APPROPRIATE")
    # Temporal x is better as line
    if x.role == "temporal":
        return GateResult(False, f"x '{spec.x}' is temporal — line is more appropriate than bar", "APPROPRIATE")
    # Very high cardinality → horizontal bar more appropriate
    if x.cardinality > 30:
        # Still appropriate but downgrade — for gate we pass but note; if strict bar, suggest h_bar
        if spec.chart_type == "bar":
            return GateResult(True, "APPROPRIATE: bar ok but h_bar preferred for 30+ cats (soft)", "APPROPRIATE")
    # Identifier
    if x.role == "identifier":
        return GateResult(False, "x is identifier, not appropriate for bar", "APPROPRIATE")
    # Zero variance y
    y = _get(p, spec.y)
    if y and y.variance_zero:
        return GateResult(False, "y has zero variance, bar not appropriate", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: bar suitable", "APPROPRIATE")

def _app_grouped(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get(p, spec.x)
    g = _get(p, spec.group_by)
    if not x or not g:
        return GateResult(False, "Missing x/group_by for grouped appropriateness", "APPROPRIATE")
    if x.cardinality * g.cardinality > 40:
        return GateResult(False, f"Too many combined categories {x.cardinality}×{g.cardinality} >40 for grouped", "APPROPRIATE")
    if x.cardinality > 12:
        return GateResult(False, "x cardinality >12 not appropriate for grouped bar", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: grouped ok", "APPROPRIATE")

def _app_line(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get(p, spec.x)
    y = _get(p, spec.y)
    if not x or not y:
        return GateResult(False, "Missing x/y for line", "APPROPRIATE")
    if x.role not in {"temporal"} and not x.ordered:
        # Categorical unordered with many points is not ideal for line
        if x.cardinality > 15:
            return GateResult(False, f"x '{spec.x}' unordered with {x.cardinality} cats — bar more appropriate than line", "APPROPRIATE")
    if y.variance_zero:
        return GateResult(False, "y zero variance, line not appropriate", "APPROPRIATE")
    if x.cardinality < 3:
        return GateResult(False, "Line needs ≥3 distinct x for trend", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: line/area suitable", "APPROPRIATE")

def _app_scatter(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get(p, spec.x); y = _get(p, spec.y)
    if x and x.variance_zero or y and y.variance_zero:
        return GateResult(False, "Zero variance axis, scatter not appropriate", "APPROPRIATE")
    if x and x.cardinality < 3 or y and y.cardinality < 3:
        return GateResult(False, "Need ≥3 distinct values per axis for scatter", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: scatter suitable", "APPROPRIATE")

def _app_histogram(spec: ChartSpec, p: DataProfile) -> GateResult:
    col = _get(p, spec.x) or _get(p, spec.y)
    if not col:
        return GateResult(False, "Histogram needs numeric col", "APPROPRIATE")
    if col.variance_zero:
        return GateResult(False, "Zero variance, histogram not appropriate", "APPROPRIATE")
    if col.cardinality < 4:
        return GateResult(False, f"Only {col.cardinality} distinct values — histogram not appropriate (use bar)", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: histogram suitable", "APPROPRIATE")

def _app_boxplot(spec: ChartSpec, p: DataProfile) -> GateResult:
    y = _get(p, spec.y) or _get(p, spec.x)
    if y and y.variance_zero:
        return GateResult(False, "Zero variance, box not appropriate", "APPROPRIATE")
    # If x categorical, need reasonable groups
    if spec.x and spec.y:
        x = _get(p, spec.x)
        if x and x.role == "numeric":
            # x numeric not ideal for box grouping
            return GateResult(False, "Box grouping x should be categorical", "APPROPRIATE")
        if x and x.cardinality > 10:
            return GateResult(False, "Too many box groups >10", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: box suitable", "APPROPRIATE")

def _app_heatmap(spec: ChartSpec, p: DataProfile) -> GateResult:
    # Correlation heatmap: need variance per numeric
    zero_var = sum(1 for n in p.numeric_cols if (p.by_name(n) and p.by_name(n).variance_zero))
    if zero_var == len(p.numeric_cols):
        return GateResult(False, "All numeric cols zero variance, heatmap not appropriate", "APPROPRIATE")
    if len(p.numeric_cols) < 3:
        return GateResult(False, "Heatmap needs ≥3 numerics for correlation", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: heatmap suitable", "APPROPRIATE")

def _app_pie(spec: ChartSpec, p: DataProfile) -> GateResult:
    x = _get(p, spec.x)
    y = _get(p, spec.y) if spec.y else None
    if not x:
        return GateResult(False, "Pie needs x", "APPROPRIATE")
    if x.cardinality < 2:
        return GateResult(False, "Pie needs 2-6 categories", "APPROPRIATE")
    if x.role == "temporal":
        return GateResult(False, "Temporal x not appropriate for pie", "APPROPRIATE")
    if x.role == "identifier":
        return GateResult(False, "Identifier x not appropriate for pie", "APPROPRIATE")
    if y and y.has_negatives:
        return GateResult(False, "Pie requires non-negative y (has negatives)", "APPROPRIATE")
    if y and y.variance_zero:
        return GateResult(False, "Zero variance y, pie not appropriate", "APPROPRIATE")
    # Many categories: still appropriate via bucketing but will be scored lower
    if x.cardinality > 6:
        return GateResult(True, f"APPROPRIATE: pie with {x.cardinality} cats feasible via bucketing (6 max ideal)", "APPROPRIATE")
    return GateResult(True, "APPROPRIATE: pie suitable (2-6 cats, non-negative)", "APPROPRIATE")
