"""Quality-aware dashboard curation with analytical, not cosmetic, diversity."""

from __future__ import annotations

from dataclasses import dataclass

from schemas import ChartSpec


@dataclass
class RankedCandidate:
    spec: ChartSpec
    score: float
    goal: str
    reason: str
    breakdown: dict
    gate_reasons: list[str]


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def candidate_signature(c: RankedCandidate) -> set[str]:
    s = c.spec
    return {v for v in (s.sheet, c.goal, s.x, s.y, s.group_by) if v}


def select_diverse(candidates: list[RankedCandidate], k_soft: int = 5, deduplicate_views: bool = True) -> list[RankedCandidate]:
    """Choose at most k_soft useful, distinct questions; never pad with duplicates."""
    pool = sorted((c for c in candidates if c.score >= 40 and c.spec.status == "planned"),
                  key=lambda c: (-c.score, c.spec.sheet, c.spec.title, c.spec.chart_type))
    selected: list[RankedCandidate] = []
    seen = set()
    while pool and len(selected) < min(k_soft, 8):
        def utility(c):
            goals = sum(s.goal == c.goal for s in selected)
            metric = sum(bool(c.spec.y) and s.spec.y == c.spec.y for s in selected)
            overlap = max((jaccard(candidate_signature(c), candidate_signature(s)) for s in selected), default=0)
            return c.score - goals * 12 - metric * 5 - overlap * 8
        best = max(pool, key=utility)
        pool.remove(best)
        s = best.spec
        # Changing orientation or switching a bar to pie does not answer a
        # new question. Raw distributions and relationships remain distinct.
        family = "aggregate" if s.chart_type in {"bar", "horizontal_bar", "pie", "donut", "grouped_bar", "stacked_bar", "stacked_100"} else best.goal
        if not deduplicate_views:
            family = s.chart_type
        key = (s.sheet, family, s.x, s.y, s.group_by, s.agg_function, s.filter)
        if key in seen:
            continue
        seen.add(key)
        selected.append(best)
    return selected
