"""Diversity ranking — curate Top-K without redundant analytical questions."""

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
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

def candidate_signature(c: RankedCandidate) -> set[str]:
    s = c.spec
    return {s.chart_type, s.goal if hasattr(s, "goal") else c.goal, s.x or "", s.y or "", s.group_by or ""}

def select_diverse(candidates: list[RankedCandidate], k_soft: int = 5) -> list[RankedCandidate]:
    """MMR-like selection: quality + diversity.

    Soft target: aim around k_soft but allow fewer (quality) or more (rich dataset).
    Quality threshold: score ≥40. Diversity: penalize overlapping (goal,x,y) signatures.
    """
    if not candidates:
        return []
    # Sort by score desc
    sorted_c = sorted(candidates, key=lambda x: x.score, reverse=True)
    # Filter low quality?
    qualified = [c for c in sorted_c if c.score >= 35]
    if not qualified:
        qualified = sorted_c[:1]

    # If exploratory, aim for diverse goals first
    selected: list[RankedCandidate] = []
    for cand in qualified:
        if len(selected) >= 8:  # hard cap
            break
        # Check redundancy vs selected
        sig = candidate_signature(cand)
        max_sim = max((jaccard(sig, candidate_signature(s)) for s in selected), default=0.0)
        # If too similar to an already selected (same goal + same columns), skip unless score much higher
        if max_sim >= 0.8:
            continue
        # Prefer diversity of goals
        goals = {s.goal for s in selected}
        # If we already have 2 of same goal, prefer new goal unless score is top 2
        if cand.goal in goals and len([s for s in selected if s.goal == cand.goal]) >= 2:
            # allow if candidate is in top 3 overall
            if cand not in sorted_c[:3]:
                continue
        selected.append(cand)
        # Soft target: if we have k_soft and remaining candidates are much lower score, stop
        if len(selected) >= k_soft:
            # If next best is >15 points lower than last selected, stop
            next_idx = len([c for c in qualified if c not in selected])
            # simple heuristic: stop if we have enough diverse goals
            if len(selected) >= k_soft and len(goals | {cand.goal}) >= 3:
                # Check if remaining top score is significantly lower
                remaining = [c for c in qualified if c not in selected]
                if remaining and (selected[-1].score - remaining[0].score) > 20:
                    break

    # If we ended with <2 and dataset is rich (many qualified), add at least 2
    if len(selected) < 2 and len(qualified) >= 2:
        for c in qualified:
            if c not in selected:
                selected.append(c)
            if len(selected) >= 2:
                break

    # Soft target flexibility: simple dataset → keep 2-3, rich → allow up to 7-8 already capped
    return selected
