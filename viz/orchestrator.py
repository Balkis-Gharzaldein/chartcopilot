"""Orchestrator: profiler → intent → candidates → gates → scoring → ranking."""

from __future__ import annotations

from schemas import ChartSpec, SheetProfile
from viz.profiler import profile_data
from viz.intent import parse_intents
from viz.candidates import generate_for_intent, generate_exploratory
from viz.gates.can import can_gate
from viz.gates.appropriate import appropriate_gate
from viz.gates.useful import useful_gate
from viz.scoring import score_candidate
from viz.ranking import RankedCandidate, select_diverse
import re

def _detect_explicit_request(raw: str) -> bool:
    # Explicit if mentions chart type directly
    low = raw.lower()
    for kw in ["bar", "line", "scatter", "pie", "donut", "heatmap", "histogram", "box", "area", "stacked", "grouped"]:
        if kw in low:
            return True
    return bool(re.search(r"\b(make|change|use|show).*(bar|line|scatter|pie|donut|heatmap|histogram|box|area|stacked|grouped)", low))

def orchestrate(profiles: list[SheetProfile], frames: dict | None, lines: list[str]) -> list[ChartSpec]:
    """Main entry: return ranked, validated specs for given guideline lines."""
    if not profiles:
        return []

    def _is_instructions(n: str) -> bool:
        return "instruction" in n.lower() or "guideline" in n.lower()
    data_profiles = [p for p in profiles if p.columns and not _is_instructions(p.sheet_name)]
    if not data_profiles:
        data_profiles = [p for p in profiles if p.columns]
        if not data_profiles:
            return []

    intents = parse_intents(lines, profiles)
    all_ranked: list[RankedCandidate] = []

    for prof in data_profiles:
        # Build enriched profile with DataFrame if available
        df = frames.get(prof.sheet_name) if frames else None
        from viz.profiler import profile_data
        dprof = profile_data(prof, df)

        # Handle exploratory overview
        exploratory_intents = [it for it in intents if it.is_exploratory]
        regular_intents = [it for it in intents if not it.is_exploratory]

        if exploratory_intents:
            # Generate exploratory candidates (first-class) — each with its own analytical goal
            exp_specs = generate_exploratory(dprof, prof.sheet_name)
            # Map chart type to goal for diversity
            goal_map = {
                "bar": "comparison", "horizontal_bar": "ranking", "grouped_bar": "comparison",
                "stacked_bar": "composition", "stacked_100": "composition",
                "line": "trend", "area": "trend",
                "histogram": "distribution", "boxplot": "distribution",
                "scatter": "relationship", "heatmap": "correlation",
                "pie": "composition", "donut": "composition",
            }
            for spec in exp_specs:
                can = can_gate(spec, dprof)
                if not can.passed:
                    continue
                app = appropriate_gate(spec, dprof)
                if not app.passed:
                    continue
                g = goal_map.get(spec.chart_type, "overview")
                useful = useful_gate(spec, dprof, g)
                if not useful.passed:
                    continue
                explicit = False
                score, reason, breakdown = score_candidate(spec, dprof, g, explicit)
                all_ranked.append(RankedCandidate(spec=spec, score=score, goal=g, reason=reason, breakdown=breakdown, gate_reasons=[can.reason, app.reason]))

        # Regular intents: generate per intent
        for idx, intent in enumerate(regular_intents):
            specs = generate_for_intent(intent, dprof, prof.sheet_name, idx_offset=idx*10)
            for spec in specs:
                if spec.status == "skipped":
                    all_ranked.append(RankedCandidate(spec=spec, score=10, goal=intent.goal, reason=spec.skip_reason or "skipped", breakdown={}, gate_reasons=[spec.skip_reason or ""]))
                    continue
                can = can_gate(spec, dprof)
                if not can.passed:
                    # Keep as skipped? For orchestrator we filter, but planning will create skipped for invalid explicit requests
                    # If explicit request, keep as skipped with reason instead of dropping
                    if _detect_explicit_request(intent.raw):
                        spec.status = "skipped"
                        spec.skip_reason = f"CAN gate: {can.reason}"
                        # Keep with low score for transparency
                        all_ranked.append(RankedCandidate(spec=spec, score=15, goal=intent.goal, reason=can.reason, breakdown={}, gate_reasons=[can.reason]))
                    continue
                app = appropriate_gate(spec, dprof)
                if not app.passed:
                    if _detect_explicit_request(intent.raw):
                        spec.status = "skipped"
                        spec.skip_reason = f"APPROPRIATE gate: {app.reason}. Closest alternative: bar chart."
                        all_ranked.append(RankedCandidate(spec=spec, score=20, goal=intent.goal, reason=app.reason, breakdown={}, gate_reasons=[app.reason]))
                        continue
                    # Otherwise drop candidate
                    continue
                useful = useful_gate(spec, dprof, intent.goal)
                if not useful.passed:
                    continue
                explicit = _detect_explicit_request(intent.raw) and spec.chart_type == (intent.explicit_chart_type or spec.chart_type)
                score, reason, breakdown = score_candidate(spec, dprof, intent.goal, explicit)
                all_ranked.append(RankedCandidate(spec=spec, score=score, goal=intent.goal, reason=reason, breakdown=breakdown, gate_reasons=[can.reason, app.reason]))

    if not all_ranked:
        # Fallback: try at least one exploratory candidate as skipped? Return empty
        return []

    # Separate skipped vs planned for ranking
    planned_ranked = [c for c in all_ranked if c.spec.status != "skipped"]
    skipped_ranked = [c for c in all_ranked if c.spec.status == "skipped"]

    # Diversity selection on planned only
    # Soft target: aim for 3-6, but allow fewer/more based on quality
    selected = select_diverse(planned_ranked, k_soft=5)

    # Return specs sorted by score desc, with skipped appended
    result_specs: list[ChartSpec] = [c.spec for c in selected]
    # Attach score/reason as data_notes for explainability? Use spec.data_notes to preserve
    for cand in selected:
        # Ensure explainability preserved
        extra = f" [score {cand.score:.0f}: {cand.reason}]"
        if cand.spec.data_notes:
            if "score" not in cand.spec.data_notes:
                cand.spec.data_notes = (cand.spec.data_notes + extra).strip()
        else:
            cand.spec.data_notes = extra.strip()
        # Also store goal in title if not already? Keep title as is

    # Add skipped
    for c in skipped_ranked:
        result_specs.append(c.spec)

    return result_specs
