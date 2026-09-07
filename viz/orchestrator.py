"""Orchestrator: profiler → intent → candidates → gates → scoring → ranking.

Extended for VizSpec 7-key workflow: Intent, Dimensions, Metrics, Filter, Conditional highlighting, Expected chart, TimeBucket.
Supports up to 2 charts per request (dual And), reject if >2 metrics/charts.
Uses live profiler DataProfile for classification (no LLM re-inference) and data-to-viz decision tree.
"""

from __future__ import annotations

from schemas import ChartSpec, SheetProfile
from viz.profiler import profile_data
from viz.intent import parse_intents, parse_vizspec
from viz.candidates import generate_for_intent, generate_exploratory
from viz.gates.can import can_gate
from viz.gates.appropriate import appropriate_gate
from viz.gates.useful import useful_gate
from viz.scoring import score_candidate
from viz.ranking import RankedCandidate, select_diverse
from viz.decision_tree import decision_tree_candidates, classify_data_profile
import re

def _detect_explicit_request(raw: str) -> bool:
    # Explicit if mentions chart type directly
    low = raw.lower()
    for kw in ["bar", "line", "scatter", "pie", "donut", "heatmap", "histogram", "box", "area", "stacked", "grouped"]:
        if kw in low:
            return True
    return bool(re.search(r"\b(make|change|use|show).*(bar|line|scatter|pie|donut|heatmap|histogram|box|area|stacked|grouped)", low))

def _validate_vizspec(vizspec: dict, profiles: list[SheetProfile]) -> str | None:
    """Validate VizSpec columns exist. Returns skip reason if invalid, else None.
    Soft auto-correct synonyms (e.g., sales≈revenue), hard skip on hallucinations with Did you mean.
    Uses live DataProfile via profiles (SheetProfile) + synonym map from planning.py.
    """
    from viz.resolve import resolve_column as _resolve, suggest_columns as _suggest
    all_cols = [c.name for p in profiles for c in p.columns]
    all_cols_set = set(all_cols)
    all_cols_lower = {c.lower(): c for c in all_cols}
    # Role map inferred like the profiler (dtype + samples), used as soft
    # bias by the single resolver (viz/resolve.py). No hard filtering here.
    def _infer_role(col: str) -> str:
        for p in profiles:
            for c in p.columns:
                if c.name == col:
                    if c.dtype.lower() in ("int64", "int32", "float64", "float32", "int16", "float16"):
                        return "numeric"
                    if any(tok in c.name.lower() for tok in ["date", "time", "year", "month"]):
                        return "temporal"
                    try:
                        cleaned = [str(v).replace("$", "").replace(",", "").strip() for v in c.sample_values[:3] if v]
                        if cleaned and all(_is_float_local(s) for s in cleaned if s):
                            return "numeric"
                        return "categorical"
                    except Exception:
                        return "categorical"
        return "categorical"

    _role_map = {c: _infer_role(c) for c in all_cols}

    def _find_closest(query: str, prefer_role: str | None = None) -> str | None:
        # Single resolver: exact > substring + token overlap + alias
        # concepts (phrase-vs-token bug fixed in viz/resolve.py).
        col, _score = _resolve(query, all_cols, _role_map, prefer_role)
        return col

    def _suggest_for(query: str) -> str | None:
        sugs = _suggest(query, all_cols, _role_map)
        return sugs[0] if sugs else None

    def _is_float_local(s: str) -> bool:
        try:
            float(s)
            return True
        except Exception:
            return False

    # Validate dimensions with soft auto-correct, allow time_bucket derived dims like month/quarter/year
    time_bucket = vizspec.get("time_bucket", "none")
    has_temporal = any("date" in c.lower() or "time" in c.lower() or "year" in c.lower() or "month" in c.lower() for c in all_cols)
    for idx, dim in enumerate(list(vizspec.get("dimensions", []))):
        if dim in all_cols_set or dim.lower() in all_cols_lower:
            continue
        # Allow derived time_bucket dims (month/quarter/year) if temporal column exists and bucket matches
        dim_low = dim.lower()
        if dim_low in ("month", "quarter", "year", "week", "day") and time_bucket != "none" and has_temporal:
            continue
        if dim_low in ("month", "quarter", "year") and time_bucket in ("monthly", "quarterly", "yearly") and has_temporal:
            continue
        # Try alias-aware auto-correct (single resolver)
        corrected = _find_closest(dim)
        if corrected and corrected.lower() != dim.lower():
            vizspec["dimensions"][idx] = corrected
            continue
        suggestion = _suggest_for(dim)
        available = ", ".join(all_cols)
        if suggestion:
            return f"Dimension column '{dim}' not found in dataset. Did you mean '{suggestion}'? Available columns: {available}."
        return f"Dimension column '{dim}' not found in dataset. Available columns: {available}."
    # Validate metrics
    for m in vizspec.get("metrics", []):
        expr = m.get("expr", "") if isinstance(m, dict) else str(m)
        import re as _re
        col = _re.sub(r".*\((.*)\).*", r"\1", expr) if "(" in expr else expr
        col = col.strip().strip("\"'`")
        if not col:
            continue
        if col not in all_cols_set and col.lower() not in all_cols_lower:
            # Allow derived like retention_rate
            if "retention" in col.lower() or "rate" in col.lower():
                continue
            # Try alias-aware auto-correct (e.g. revenue -> GROSS AMT)
            corrected = _find_closest(col)
            if corrected and corrected.lower() != col.lower():
                # Soft correct: update metrics expr to actual column, keep original agg
                if isinstance(m, dict):
                    # Preserve SUM() wrapper if present
                    if "(" in expr:
                        m["expr"] = expr.replace(col, corrected)
                    else:
                        m["expr"] = corrected
                continue
            suggestion = _suggest_for(col)
            available = ", ".join(all_cols)
            if suggestion:
                return f"Metric column '{col}' not found in dataset. Did you mean '{suggestion}'? Available columns: {available}."
            return f"Metric column '{col}' not found in dataset. Available columns: {available}."
    # Validate filters and conditional fields
    for f in vizspec.get("filters", []):
        if not isinstance(f, dict):
            continue
        for key in ("col", "rank_by"):
            col = f.get(key)
            if col and col not in all_cols_set and col.lower() not in all_cols_lower:
                # rank_by may be an expr like SUM(GROSS AMT) — extract inner
                # column and accept it verbatim when it exists.
                from viz.resolve import extract_col_from_expr as _extract

                inner = _extract(str(col)) if key == "rank_by" else str(col)
                if inner in all_cols_set or inner.lower() in all_cols_lower:
                    if key == "rank_by":
                        exact = inner if inner in all_cols_set else all_cols_lower[inner.lower()]
                        f[key] = f"SUM({exact})"
                    else:
                        f[key] = inner if inner in all_cols_set else all_cols_lower[inner.lower()]
                    continue
                corrected = _find_closest(inner)
                if corrected and corrected.lower() != inner.lower():
                    f[key] = corrected if key == "col" else f"SUM({corrected})"
                    continue
                suggestion = _suggest_for(inner)
                if suggestion and suggestion.lower() != inner.lower():
                    return f"Filter column '{col}' not found. Did you mean '{suggestion}'?"
                return f"Filter column '{col}' not found."
    cond = vizspec.get("conditional_highlighting")
    if isinstance(cond, dict) and cond.get("field"):
        col = cond.get("field")
        if col and col not in all_cols_set and col.lower() not in all_cols_lower:
            corrected = _find_closest(str(col))
            if corrected and corrected.lower() != str(col).lower():
                cond["field"] = corrected
            else:
                suggestion = _find_closest(str(col))
                if suggestion:
                    return f"Conditional field '{col}' not found. Did you mean '{suggestion}'?"
                return f"Conditional field '{col}' not found."
    if len(vizspec.get("metrics", [])) > 2:
        return "Too many metrics (>2) — up to 2 charts per request."
    return None


def _vizspec_to_intent(vizspec: dict, raw: str) -> object:
    """Convert VizSpec to AnalyticalIntent-like object for candidate generation."""
    from viz.intent import AnalyticalIntent
    intent_list = vizspec.get("intent", ["comparison"])
    primary = intent_list[0] if isinstance(intent_list, list) and intent_list else "comparison"
    exp_chart = None
    ec = vizspec.get("expected_chart") or {}
    if isinstance(ec, dict):
        # Only a concrete chart_type counts as an explicit request. The
        # family ("ranking", "comparison", …) is a goal label — leaking it
        # into explicit_chart_type made generate_for_intent return [] (no
        # generator branch matches "ranking"), which then fell through to a
        # decision-tree default of the wrong family (line for a ranking).
        exp_chart = ec.get("chart_type")
    # Map time_bucket to explicit handling via intent
    wants_top_n = None
    wants_split = False
    for f in vizspec.get("filters", []):
        if isinstance(f, dict) and f.get("op") in ("in_top_n", "top_n"):
            try:
                wants_top_n = int(f.get("value", 5))
            except Exception:
                wants_top_n = 5
        if isinstance(f, dict) and f.get("op") == "split":
            wants_split = True
    return AnalyticalIntent(
        raw=raw,
        goal=primary,
        explicit_chart_type=exp_chart,
        explicit_agg=None,
        wants_top_n=wants_top_n,
        wants_split=wants_split,
        is_exploratory=False,
        group_lines=[raw],
        filters=vizspec.get("filters"),
        entities=vizspec.get("dimensions", []) + [m.get("expr","") for m in vizspec.get("metrics", []) if isinstance(m, dict)],
        confidence=vizspec.get("confidence", 0.85),
        reasoning=vizspec.get("reasoning") or f"VizSpec: {vizspec.get('title','')}",
    )


def _metric_col_of(expr: str) -> str:
    """Inner column of a metric expr: SUM(GROSS AMT) -> GROSS AMT."""
    import re as _re

    e = (expr or "").strip().strip("\"'`")
    if "(" in e and ")" in e:
        m = _re.search(r"\((.*)\)", e)
        if m:
            return m.group(1).strip().strip("\"'`")
    return e


def _ensure_dual_coverage(
    vizspecs: list[dict],
    raw: str,
    profiles: list[SheetProfile],
    data_profiles_map: dict | None,
) -> list[dict]:
    """Normalize + complete VizSpecs so every measure a question names is
    displayed (≤2 charts per request).

    Two failure modes fixed:
    (a) one VizSpec carrying 2 metrics renders a single chart — the second
        metric vanishes. Split into single-metric VizSpecs sharing dims.
    (b) the LLM merges "A by X, and how many Y" into one VizSpec covering
        one measure. Resolve the question's measures independently and
        append a complementary VizSpec (shared dims + top-N filter) for any
        named measure displayed nowhere.
    No-ops for single-measure questions and already-covered duals.
    """
    if not vizspecs or not (raw or "").strip():
        return vizspecs

    # (a) split multi-metric singles
    normed: list[dict] = []
    for v in vizspecs:
        ms = v.get("metrics", []) or []
        if len(ms) <= 1:
            normed.append(v)
            continue
        for m in ms[:2]:
            c = dict(v)
            c["metrics"] = [m]
            normed.append(c)
        if len(ms) > 2:
            rest = dict(v)
            rest["metrics"] = ms[2:]
            normed.append(rest)

    # displayed columns: single-metric spec shows its metric
    displayed = {_metric_col_of(
        (m.get("expr", "") if isinstance(m, dict) else str(m))
    ).lower() for v in normed for m in (v.get("metrics", [])[:1])}
    displayed.discard("")

    # (b) measures named by the question (whole-phrase score, alias-aware)
    try:
        from viz.resolve import score_candidate as _rs

        num_cols: list[str] = []
        if data_profiles_map:
            for dp in data_profiles_map.values():
                for n in getattr(dp, "numeric_cols", []):
                    if n not in num_cols:
                        num_cols.append(n)
        if not num_cols:
            num_cols = [c.name for p in profiles for c in p.columns]
        named = [c for c in num_cols if _rs(raw, c) >= 2.0][:2]
    except Exception:
        named = []
    if len(named) < 2:
        return normed

    base = normed[0] if normed else {}
    for col in named:
        if len(normed) >= 2:
            break
        if col.lower() in displayed:
            continue
        normed.append({
            "intent": ["comparison"],
            "dimensions": list(base.get("dimensions", []))[:1],
            "metrics": [{"expr": col, "agg": "sum"}],
            "filters": [dict(f) for f in base.get("filters", []) if isinstance(f, dict)],
            "conditional_highlighting": None,
            "expected_chart": None,
            "time_bucket": base.get("time_bucket", "none"),
            "title": raw[:90],
            "confidence": 0.5,
            "reasoning": "Dual-coverage complement: the question names a second measure the parsed VizSpecs omit.",
        })
        displayed.add(col.lower())
    return normed


def orchestrate(profiles: list[SheetProfile], frames: dict | None, lines: list[str]) -> list[ChartSpec]:
    """Main entry: return ranked, validated specs for given guideline lines.
    Supports VizSpec 7-key workflow (up to 2 charts), falls back to intent workflow.
    """
    if not profiles:
        return []

    def _is_instructions(n: str) -> bool:
        return "instruction" in n.lower() or "guideline" in n.lower()
    data_profiles = [p for p in profiles if p.columns and not _is_instructions(p.sheet_name)]
    if not data_profiles:
        data_profiles = [p for p in profiles if p.columns]
        if not data_profiles:
            return []

    # Try VizSpec path first (7 keys) — LLM VizSpec parsing with live profiler grounding
    # Build live DataProfile eagerly before LLM (per user Q3 approve: Live DataProfile)
    data_profiles_map = None
    try:
        from viz.profiler import profile_workbook
        data_profiles_map = profile_workbook(profiles, frames)
    except Exception:
        data_profiles_map = None
    vizspecs = parse_vizspec(lines, profiles, data_profiles_map)
    if vizspecs:
        # Dual-coverage: one named measure must never vanish into an LLM
        # merge (single question only; multi-line guideline flow untouched).
        if len(lines) == 1 and lines[0].strip():
            try:
                vizspecs = _ensure_dual_coverage(vizspecs, lines[0], profiles, data_profiles_map)
            except Exception:
                pass
        # Enforce up to 2 charts per request (Q2 APPROVE)
        total_metrics = sum(len(v.get("metrics", [])) for v in vizspecs)
        # Already capped in parse_vizspec, but double-check total vizspecs count
        if len(vizspecs) > 2:
            vizspecs = vizspecs[:2]
        if total_metrics > 2:
            # Reject extras already trimmed in parse_vizspec; if still >2, return skipped for transparency
            return [
                ChartSpec(
                    id=f"spec_reject_{i}",
                    sheet=profiles[0].sheet_name if profiles else "data",
                    chart_type="bar",
                    title=v.get("title") or lines[0][:90] if lines else "Rejected",
                    x=None, y=None, status="skipped",
                    skip_reason=f"Too many metrics/charts ({total_metrics}) — up to 2 per request (per user Q2)."
                ) for i, v in enumerate(vizspecs)
            ]
        # Validate each vizspec columns exist
        for vs in vizspecs:
            reason = _validate_vizspec(vs, profiles)
            if reason:
                # Return skipped for invalid VizSpec
                return [
                    ChartSpec(
                        id="spec_skipped_validation",
                        sheet=profiles[0].sheet_name if profiles else "data",
                        chart_type="bar",
                        title=vs.get("title") or lines[0][:90] if lines else "Validation failed",
                        x=None, y=None, status="skipped", skip_reason=reason
                    )
                ]
        # VizSpec workflow will be handled below per-vizspec candidate generation with decision tree
        # Fall through to vizspec-aware candidate generation
        intents = []  # will be populated from vizspecs below
        vizspec_mode = True
    else:
        vizspecs = []
        vizspec_mode = False
        # Parse intents: LLM-first reasoning, deterministic fallback; single-question = single intent (do not split)
        intents = parse_intents(lines, profiles)
    # Enforce one-request-at-a-time: if multiple lines provided, treat as single question (per product decision)
    # Caller should send one question; if multiple, we keep as separate intents but downstream will select 1 per intent
    # For ask Question we enforce exactly 1 intent when lines is single
    if len(lines) == 1 and len(intents) > 1:
        intents = intents[:1]
        intents[0].raw = lines[0]
        intents[0].group_lines = [lines[0]]

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

        # VizSpec mode: generate per VizSpec (7 keys) with decision tree + live profiler classification
        if vizspec_mode and vizspecs:
            for v_idx, vizspec in enumerate(vizspecs):
                # Convert VizSpec to intent-like for candidate generation
                raw = lines[v_idx] if v_idx < len(lines) else lines[0] if lines else (vizspec.get("title") or "")
                intent = _vizspec_to_intent(vizspec, raw)  # type: ignore[arg-type]
                # Decision tree candidate families (live profiler, Q3)
                try:
                    tree_candidates = decision_tree_candidates(dprof, vizspec)
                except Exception:
                    tree_candidates = []
                # Generate candidates via existing generator (goal-driven, data-aware)
                specs = generate_for_intent(intent, dprof, prof.sheet_name, idx_offset=v_idx * 10)
                # Fallback: if generator returns empty (e.g., variance_zero filtered), create direct spec from VizSpec
                if not specs or all(s.status == "skipped" for s in specs):
                    viz_dims = vizspec.get("dimensions", [])
                    viz_metrics = vizspec.get("metrics", [])
                    viz_time = vizspec.get("time_bucket", "none")
                    viz_exp = vizspec.get("expected_chart") or {}
                    # Direct construction from VizSpec
                    valid_cols = [c.name for c in prof.columns]
                    temporal_dims = [d for d in viz_dims if d in dprof.temporal_cols]
                    if (vizspec.get("time_bucket") != "none" or intent.goal == "trend") and temporal_dims:
                        x = temporal_dims[0]
                    else:
                        x = viz_dims[0] if viz_dims and viz_dims[0] in valid_cols else (dprof.categorical_cols[0] if dprof.categorical_cols else None)
                    y = None
                    agg = "sum"
                    if viz_metrics and isinstance(viz_metrics[0], dict):
                        expr = viz_metrics[0].get("expr","")
                        import re as _re3
                        col = _re3.sub(r".*\((.*)\).*", r"\1", expr) if "(" in expr else expr
                        col = col.strip().strip("\"'`")
                        if col in [c.name for c in prof.columns]:
                            y = col
                            agg = viz_metrics[0].get("agg") or "sum"
                    ctype = viz_exp.get("chart_type") if viz_exp and viz_exp.get("chart_type") in ["bar","pie","line","scatter","grouped_bar","stacked_bar","stacked_100","area","histogram","boxplot","heatmap","donut","horizontal_bar"] else (
                        "line" if (vizspec.get("time_bucket") != "none" or intent.goal == "trend") and temporal_dims
                        else (tree_candidates[0] if tree_candidates else "bar")
                    )
                    title = vizspec.get("title") or intent.raw[:90] or f"{y or 'count'} by {x}" if x else "Chart"
                    specs = [ChartSpec(id=f"spec_{v_idx*10+1}_{ctype}", sheet=prof.sheet_name, chart_type=ctype, title=title[:90], x=x, y=y, agg_function=agg, status="planned")]
                    # Re-apply dimensions/metrics already set, continue to override loop below
                # Apply VizSpec overrides: dimensions, metrics, time_bucket, filters, expected_chart
                viz_dims = vizspec.get("dimensions", [])
                viz_metrics = vizspec.get("metrics", [])
                viz_time = vizspec.get("time_bucket", "none")
                viz_exp = vizspec.get("expected_chart") or {}
                viz_filters = vizspec.get("filters", [])
                viz_cond = vizspec.get("conditional_highlighting")
                for spec in specs:
                    # Override x/y/group_by from VizSpec dimensions if provided and columns exist
                    # Handle time_bucket + trend: ensure temporal dimension is x, categorical is group_by
                    # Use GROUP_AWARE guard: bar/pie should not have group_by
                    GROUP_AWARE_VIZ = {"grouped_bar","stacked_bar","stacked_100","line","area","scatter","heatmap","boxplot"}
                    if viz_dims:
                        # Determine temporal vs categorical via profiler
                        temp_dims = [d for d in viz_dims if d in dprof.temporal_cols]
                        cat_dims = [d for d in viz_dims if d in dprof.categorical_cols or d in dprof.identifier_cols]
                        # Check if VizSpec intent is trend and time_bucket specified
                        is_trend = any("trend" in str(g).lower() for g in vizspec.get("intent", [])) if isinstance(vizspec.get("intent"), list) else "trend" in str(vizspec.get("intent", "")).lower()
                        viz_time_bucket = vizspec.get("time_bucket", "none")
                        if is_trend and viz_time_bucket != "none" and temp_dims:
                            # For trend, x should be temporal, group_by categorical (only if chart supports it)
                            spec.x = temp_dims[0]
                            if spec.chart_type in GROUP_AWARE_VIZ and cat_dims:
                                spec.group_by = cat_dims[0]
                            elif spec.chart_type in GROUP_AWARE_VIZ and len(viz_dims) > 1 and viz_dims[1] in [c.name for c in prof.columns]:
                                spec.group_by = viz_dims[1]
                            else:
                                spec.group_by = None
                        else:
                            # Default: first dimension → x, second → group_by only if chart supports grouping
                            if viz_dims[0] and viz_dims[0] in [c.name for c in prof.columns]:
                                spec.x = viz_dims[0]
                            if spec.chart_type in GROUP_AWARE_VIZ and len(viz_dims) > 1 and viz_dims[1] in [c.name for c in prof.columns] and viz_dims[1] not in (spec.x, spec.y):
                                spec.group_by = viz_dims[1]
                            else:
                                # For bar/pie/histogram, ensure no group_by
                                if spec.chart_type in ("bar","horizontal_bar","pie","donut","histogram"):
                                    spec.group_by = None
                    # Handle VizSpec metrics: pick correct y (handle ranking metric vs chart metric)
                    if viz_metrics:
                        # Determine which metric is for chart y vs ranking filter
                        # If vizspec has 2 metrics and one is used in filter rank_by, use the other as y
                        chart_metric = None
                        if len(viz_metrics) == 2 and vizspec.get("filters"):
                            rank_bys = [str(f.get("rank_by","")).lower() for f in vizspec["filters"] if isinstance(f, dict) and f.get("rank_by")]
                            for m in viz_metrics:
                                expr_low = (m.get("expr","") if isinstance(m, dict) else str(m)).lower()
                                # If this metric's expr appears in rank_by, it's ranking metric, not chart y
                                is_rank_metric = any(expr_low in rb or rb in expr_low for rb in rank_bys if rb)
                                if not is_rank_metric:
                                    chart_metric = m
                                    break
                            if not chart_metric:
                                chart_metric = viz_metrics[0] if isinstance(viz_metrics[0], dict) else {"expr": str(viz_metrics[0])}
                        else:
                            chart_metric = viz_metrics[0] if isinstance(viz_metrics[0], dict) else {"expr": str(viz_metrics[0])}
                        m0 = chart_metric if isinstance(chart_metric, dict) else {"expr": str(chart_metric)}
                        expr = m0.get("expr", "")
                        import re as _re2
                        col = _re2.sub(r".*\((.*)\).*", r"\1", expr) if "(" in expr else expr
                        col = col.strip().strip("\"'`")
                        if col and col in [c.name for c in prof.columns]:
                            spec.y = col
                            if m0.get("agg"):
                                spec.agg_function = m0.get("agg")
                            # "how many <measure>" guard: the LLM often emits
                            # agg=count for "how many units", but a resolved
                            # numeric measure means SUM(metric), not COUNT(*).
                            # Bare "how many" (categorical noun / no match)
                            # keeps count.
                            if (spec.agg_function or "").lower() == "count":
                                try:
                                    from viz.resolve import score_query as _sq

                                    _hm = re.search(
                                        r"how\s+many\s+([a-z0-9][a-z0-9 _\-]*)",
                                        raw.lower(),
                                    )
                                    if _hm:
                                        _noun = _hm.group(1).strip()
                                        _num_cols = [
                                            c.name for c in prof.columns
                                            if dprof.by_name(c.name)
                                            and dprof.by_name(c.name).role == "numeric"
                                        ] or [c.name for c in prof.columns]
                                        if _sq(_noun, col) >= 1.0 and col in _num_cols:
                                            spec.agg_function = "sum"
                                except Exception:
                                    pass
                        elif col and "retention" in col.lower():
                            # Derived metric like retention_rate may not exist as column; keep as y for execution to handle
                            spec.y = col
                            if m0.get("agg"):
                                spec.agg_function = m0.get("agg")
                    # A distinct-count metric is itself often the categorical
                    # entity (for example CUSTOMER). It must not also be used
                    # as a line-series grouping field, otherwise a monthly
                    # customer-count request becomes an unusable high-card
                    # customer chart.
                    if spec.group_by and spec.group_by == spec.y and (spec.agg_function or "").lower() == "count_distinct":
                        spec.group_by = None
                    # Time bucket → data_notes and ensure temporal x if needed
                    if viz_time and viz_time != "none":
                        if spec.data_notes:
                            spec.data_notes += f" Time bucket: {viz_time}."
                        else:
                            spec.data_notes = f"Time bucket: {viz_time}."
                        # If spec x is not temporal but bucket implies temporal, try to switch x to temporal col
                        if viz_dims and viz_dims[0] not in dprof.temporal_cols and dprof.temporal_cols:
                            # Keep vizspec dimensions as is (user explicit), don't override
                            pass
                    # Filters → human readable filter + data_notes
                    if viz_filters:
                        filt_str = "; ".join([f"{f.get('col')} {f.get('op')} {f.get('value')}" for f in viz_filters if isinstance(f, dict)])
                        if filt_str:
                            spec.filter = filt_str[:200]
                            if spec.data_notes:
                                spec.data_notes += f" Filter: {filt_str}."
                            else:
                                spec.data_notes = f"Filter: {filt_str}."
                            # Top-N pre-filter marker
                            for f in viz_filters:
                                if isinstance(f, dict) and f.get("op") in ("in_top_n", "top_n"):
                                    spec.data_notes = (spec.data_notes or "") + f" Top {f.get('value')} by {f.get('rank_by','')}."
                    # Conditional highlighting → data_notes annotation
                    if viz_cond and isinstance(viz_cond, dict) and viz_cond.get("condition"):
                        cond = viz_cond.get("condition")
                        field = viz_cond.get("field") or viz_cond.get("condition")
                        highlight = f" Conditional: {cond} (field {field or ''}, annotate)"
                        if spec.data_notes:
                            spec.data_notes += highlight
                        else:
                            spec.data_notes = highlight.strip()
                    # Expected chart → explicit type override if valid
                    if viz_exp and isinstance(viz_exp, dict) and viz_exp.get("chart_type"):
                        ct = viz_exp.get("chart_type")
                        if ct in ["bar","line","pie","scatter","grouped_bar","stacked_bar","stacked_100","area","histogram","boxplot","heatmap","donut","horizontal_bar"]:
                            spec.chart_type = ct
                    # Decision tree filtering: keep only candidates that are in tree families if tree provided
                    if tree_candidates and spec.chart_type not in tree_candidates:
                        # Don't discard immediately — keep but penalize via scoring; here we allow but scoring will handle
                        pass
                    # Mark title from VizSpec if provided
                    if vizspec.get("title") and not spec.title.startswith(vizspec["title"][:10]):
                        spec.title = vizspec["title"][:90]
                    if spec.status == "skipped":
                        all_ranked.append(RankedCandidate(spec=spec, score=10, goal=intent.goal, reason=spec.skip_reason or "skipped", breakdown={}, gate_reasons=[spec.skip_reason or ""]))
                        continue
                    can = can_gate(spec, dprof)
                    if not can.passed:
                        if _detect_explicit_request(intent.raw) or viz_exp.get("chart_type"):
                            spec.status = "skipped"
                            spec.skip_reason = f"CAN gate: {can.reason}"
                            all_ranked.append(RankedCandidate(spec=spec, score=15, goal=intent.goal, reason=can.reason, breakdown={}, gate_reasons=[can.reason]))
                        continue
                    app = appropriate_gate(spec, dprof)
                    if not app.passed:
                        if _detect_explicit_request(intent.raw) or viz_exp.get("chart_type"):
                            spec.status = "skipped"
                            spec.skip_reason = f"APPROPRIATE gate: {app.reason}."
                            all_ranked.append(RankedCandidate(spec=spec, score=20, goal=intent.goal, reason=app.reason, breakdown={}, gate_reasons=[app.reason]))
                            continue
                        continue
                    useful = useful_gate(spec, dprof, intent.goal)
                    if not useful.passed:
                        continue
                    explicit = bool(viz_exp.get("chart_type")) or (_detect_explicit_request(intent.raw) and spec.chart_type == (intent.explicit_chart_type or spec.chart_type))
                    score, reason, breakdown = score_candidate(spec, dprof, intent.goal, explicit)
                    # Blend decision tree bonus: if spec chart_type in tree candidates, boost +5
                    if tree_candidates and spec.chart_type in tree_candidates:
                        score = min(100, score + 5)
                        reason += " + tree candidate bonus"
                    all_ranked.append(RankedCandidate(spec=spec, score=score, goal=intent.goal, reason=reason, breakdown=breakdown, gate_reasons=[can.reason, app.reason]))

        # Regular intents: generate per intent (candidates internally, but return only best)
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
        # Some valid requests, especially distinct counts over time, do not
        # have a candidate because the entity metric is categorical. Build a
        # grounded direct spec instead of silently returning no chart.
        if vizspec_mode and vizspecs:
            direct: list[ChartSpec] = []
            for idx, vizspec in enumerate(vizspecs[:2]):
                prof = data_profiles[0]
                dprof = profile_data(prof, frames.get(prof.sheet_name) if frames else None)
                dims = [d for d in vizspec.get("dimensions", []) if d in [c.name for c in prof.columns]]
                time_dims = [d for d in dims if d in dprof.temporal_cols]
                x = time_dims[0] if time_dims else (dims[0] if dims else None)
                metrics = vizspec.get("metrics", [])
                y = None
                agg = "count"
                if metrics:
                    expr = metrics[0].get("expr", "") if isinstance(metrics[0], dict) else str(metrics[0])
                    y = re.sub(r".*\((.*)\).*", r"\1", expr).strip().strip("\"'`")
                    if y not in [c.name for c in prof.columns]:
                        y = None
                    if isinstance(metrics[0], dict):
                        agg = metrics[0].get("agg") or agg
                expected = vizspec.get("expected_chart") or {}
                ctype = expected.get("chart_type") if isinstance(expected, dict) else None
                if ctype not in ("line", "bar", "horizontal_bar", "pie", "scatter", "grouped_bar", "stacked_bar", "stacked_100", "area", "histogram", "boxplot", "heatmap", "donut"):
                    ctype = "line" if time_dims or vizspec.get("time_bucket") != "none" else "bar"
                direct.append(ChartSpec(
                    id=f"spec_direct_{idx + 1}",
                    sheet=prof.sheet_name,
                    chart_type=ctype,
                    title=(vizspec.get("title") or "Chart")[:90],
                    x=x,
                    y=y,
                    agg_function=agg,
                    data_notes=(f"Time bucket: {vizspec.get('time_bucket')}." if vizspec.get("time_bucket") not in (None, "none") else None),
                ))
            return direct
        return []

    # Separate skipped vs planned for ranking
    planned_ranked = [c for c in all_ranked if c.spec.status != "skipped"]
    skipped_ranked = [c for c in all_ranked if c.spec.status == "skipped"]

    # One optimal chart per question: for non-exploratory (ask), select single best overall.
    # For VizSpec dual And, allow up to 2 charts (k≤2, per Q2). For exploratory overview, allow diverse set (existing behavior). Detect mode:
    is_ask_mode = len(regular_intents) == 1 and len(exploratory_intents) == 0
    is_vizspec_dual = vizspec_mode and 1 <= len(vizspecs) <= 2 and len([v for v in vizspecs if not v.get("is_exploratory")]) <= 2
    if is_vizspec_dual and len(vizspecs) == 2:
        # Dual And → up to 2 charts, one per VizSpec (ranked per vizspec)
        if planned_ranked:
            # Group by goal/vizspec index if possible: pick top per distinct goal/vizspec, then overall top 2
            # Simplistic: sort global, pick top 2 distinct chart_type or goal
            planned_ranked.sort(key=lambda c: c.score, reverse=True)
            # Deduplicate to 2 best distinct specs (avoid duplicate same x/y)
            selected = []
            seen_ids = set()
            for cand in planned_ranked:
                key = (cand.spec.chart_type, cand.spec.x, cand.spec.y, cand.spec.group_by)
                if key not in seen_ids:
                    seen_ids.add(key)
                    selected.append(cand)
                if len(selected) >= 2:
                    break
        else:
            selected = []
    elif is_ask_mode or (vizspec_mode and len(vizspecs) == 1):
        # Data-driven selection: pick highest scoring planned candidate only (candidates evaluated internally)
        if planned_ranked:
            planned_ranked.sort(key=lambda c: c.score, reverse=True)
            selected = [planned_ranked[0]]
        else:
            selected = []
    else:
        # Diversity selection on planned only (exploratory / multi)
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
