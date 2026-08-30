"""USEFUL gates — analytical usefulness."""

from __future__ import annotations

from schemas import ChartSpec
from viz.gates.base import GateResult
from viz.profiler import DataProfile

def useful_gate(spec: ChartSpec, profile: DataProfile, intent_goal: str) -> GateResult:
    """Usefulness depends on intent + data value."""
    # Generic: very low cardinality ratio with tiny dataset is less useful?
    # For now, useful fails only for clearly non-insightful cases
    ctype = spec.chart_type
    # Check for trivial single-category
    x = profile.by_name(spec.x) if spec.x else None
    if x and x.cardinality == 1:
        return GateResult(False, "Only 1 category — not useful", "USEFUL")
    # Pie with composition goal is more useful than pie for trend
    if ctype in {"pie", "donut"} and intent_goal not in {"composition", "overview"}:
        # Not a hard fail, but we treat as soft — for gate we pass, ranking will penalize
        pass
    # Histogram with uniform distribution is less useful but still passes
    # We keep USEFUL permissive; ranking handles diversity
    return GateResult(True, f"USEFUL: {intent_goal} goal matches {ctype}", "USEFUL")
