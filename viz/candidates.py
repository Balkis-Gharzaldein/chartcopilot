"""Candidate generation — DataProfile × Intent → ChartSpec candidates."""

from __future__ import annotations

import re

from schemas import ChartSpec
from viz.profiler import DataProfile
from viz.intent import AnalyticalIntent

import re as _re2

def _norm2(s: str) -> str:
    return _re2.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def _tokens2(s: str) -> set[str]:
    return set(_norm2(s).split()) - {""}

def _col_score(intent_raw: str, col_name: str) -> float:
    # Simple scoring: exact substring boost, token overlap
    low = intent_raw.lower()
    cnorm = _norm2(col_name)
    toks = _tokens2(col_name)
    score = 0.0
    if cnorm and cnorm in low:
        score += 3.0
    for tok in toks:
        if tok and tok in _norm2(low):
            score += 1.2
    # synonym boost for common terms
    syn = {"revenue": {"revenue","sales","total","gross"}, "region": {"region","country","state"}, "product": {"product","item","sku"}, "date": {"date","time","year","month"}}
    for term, syns in syn.items():
        if term in low:
            if toks & syns:
                score += 1.0
    return score

def _best_numeric(profile: DataProfile, intent_raw: str = "", exclude: set[str] = set()) -> str | None:
    best, best_sc = None, -1
    for c in profile.columns:
        if c.name in exclude: continue
        if c.role != "numeric" or c.variance_zero:
            continue
        sc = _col_score(intent_raw, c.name) if intent_raw else 0
        # Prefer lower cardinality? Use score
        if sc > best_sc:
            best, best_sc = c.name, sc
    if best and best_sc > 0:
        return best
    # fallback: first numeric with best score or first
    for c in profile.columns:
        if c.name in exclude: continue
        if c.role == "numeric" and not c.variance_zero:
            return c.name
    for n in profile.numeric_cols:
        if n not in exclude:
            return n
    return None

def _best_categorical(profile: DataProfile, intent_raw: str = "", exclude: set[str] = set(), max_card: int = 30) -> str | None:
    candidates = [c for c in profile.columns if c.name not in exclude and c.role == "categorical"]
    if not candidates:
        return None
    # Score by intent
    scored = []
    for c in candidates:
        if not (2 <= c.cardinality <= max_card):
            continue
        sc = _col_score(intent_raw, c.name) if intent_raw else 0
        scored.append((sc, c))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1].cardinality))
        if scored[0][0] > 0:
            return scored[0][1].name
        # No strong match, pick smallest cardinality
        scored.sort(key=lambda x: x[1].cardinality)
        return scored[0][1].name
    # No filtered, fallback to any categorical
    candidates.sort(key=lambda x: x.cardinality)
    return candidates[0].name

def _has_column_match(intent_raw: str, profile: DataProfile, threshold: float = 1.2) -> bool:
    # Check if intent mentions any column with reasonable score
    max_sc = max((_col_score(intent_raw, c.name) for c in profile.columns), default=0)
    return max_sc >= threshold

def _best_temporal(profile: DataProfile) -> str | None:
    if profile.temporal_cols:
        return profile.temporal_cols[0]
    return None

def _pick_agg(intent: AnalyticalIntent, default: str = "sum") -> str:
    if intent.explicit_agg:
        return intent.explicit_agg
    if intent.goal == "distribution":
        return "count"
    if intent.goal == "correlation":
        return "mean"
    return default

def _spec_for_chart_type(ctype: str, intent: AnalyticalIntent, profile: DataProfile, sheet_name: str, sid: str) -> ChartSpec | None:
    """Build spec for explicitly requested chart type, inferring best columns via intent scoring."""
    if ctype in {"bar", "horizontal_bar", "pie", "donut"}:
        x = _best_categorical(profile, intent.raw, set(), 100)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if not x:
            return None
        if ctype in {"pie", "donut"} and not y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Share of {x}", x=x, y=None, agg_function="count")
        if y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"{y} by {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum"))
        return ChartSpec(id=sid, sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Count by {x}", x=x, y=None, agg_function="count")
    if ctype in {"grouped_bar", "stacked_bar", "stacked_100"}:
        x = _best_categorical(profile, intent.raw, set(), 100)
        g = _best_categorical(profile, intent.raw, {x} if x else set(), 12)
        y = _best_numeric(profile, intent.raw, {x, g} if x and g else {x} if x else set())
        if x and g and y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"{y} by {x} and {g}", x=x, y=y, group_by=g, agg_function=_pick_agg(intent, "sum"))
        if x and y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type="bar", title=intent.raw[:90] or f"{y} by {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum"))
        return None
    if ctype in {"line", "area"}:
        x = _best_temporal(profile) or _best_categorical(profile, intent.raw, set(), 30)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if x and y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"{y} over {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum"))
        return None
    if ctype == "scatter":
        n1 = _best_numeric(profile, intent.raw, set())
        n2 = _best_numeric(profile, intent.raw, {n1} if n1 else set())
        if n1 and n2:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type="scatter", title=intent.raw[:90] or f"{n1} vs {n2}", x=n1, y=n2)
        return None
    if ctype == "histogram":
        y = _best_numeric(profile, intent.raw, set())
        if y:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type="histogram", title=intent.raw[:90] or f"Distribution of {y}", x=y, y=None, agg_function="count")
        return None
    if ctype == "boxplot":
        y = _best_numeric(profile, intent.raw, set())
        x = _best_categorical(profile, intent.raw, {y} if y else set(), 10)
        if y:
            if x:
                return ChartSpec(id=sid, sheet=sheet_name, chart_type="boxplot", title=intent.raw[:90] or f"Distribution of {y} by {x}", x=x, y=y)
            return ChartSpec(id=sid, sheet=sheet_name, chart_type="boxplot", title=intent.raw[:90] or f"Distribution of {y}", x=y, y=None)
        return None
    if ctype == "heatmap":
        if len(profile.numeric_cols) >= 2:
            return ChartSpec(id=sid, sheet=sheet_name, chart_type="heatmap", title=intent.raw[:90] or "Correlation heatmap", x=profile.numeric_cols[0], y=profile.numeric_cols[1])
        return None
    return None


def _has_measure_match(intent_raw: str, profile: DataProfile) -> bool:
    # Check if any numeric column scores well against intent
    max_sc = max((_col_score(intent_raw, c.name) for c in profile.columns if c.role == "numeric"), default=0)
    # Also check synonyms for measure terms
    low = intent_raw.lower()
    measure_syn = {"profit","margin","revenue","sales","units","cost","total","amount","value","price","earnings","gross"}
    has_measure_word = any(w in low for w in measure_syn)
    if has_measure_word and max_sc < 1.0:
        return False
    return True

def generate_for_intent(intent: AnalyticalIntent, profile: DataProfile, sheet_name: str, idx_offset: int = 0) -> list[ChartSpec]:
    """Generate 1-4 candidates for a single intent."""
    # Conservative skip: profit margin case etc.
    if intent.goal not in ("overview",) and not _has_measure_match(intent.raw, profile):
        low = intent.raw.lower()
        if any(w in low for w in ["profit","margin"]) and "product" in low:
            return [ChartSpec(id=f"spec_{idx_offset+1}", sheet=sheet_name, chart_type="bar", title=intent.raw[:90], x=None, y=None, status="skipped", skip_reason=f"Could not map the measure term in '{intent.raw}' to any numeric column in the schema.")]

    specs: list[ChartSpec] = []
    goal = intent.goal
    ctype_explicit = intent.explicit_chart_type

    # If explicit chart type requested, generate that one first
    if ctype_explicit:
        # Even for explicit, check if measure unmappable and intent explicitly mentions profit etc. → skip
        if not _has_measure_match(intent.raw, profile) and any(w in intent.raw.lower() for w in ["profit","margin"]):
            return [ChartSpec(id=f"spec_{idx_offset+1}", sheet=sheet_name, chart_type=ctype_explicit, title=intent.raw[:90], x=None, y=None, status="skipped", skip_reason=f"Could not map the measure term in '{intent.raw}' to any numeric column in the schema.")]
        spec = _spec_for_chart_type(ctype_explicit, intent, profile, sheet_name, f"spec_{idx_offset+1}_{ctype_explicit}")
        if spec:
            specs.append(spec)
        return specs

    # Check for unmappable measure term → skipped (conservative, not guessing)
    if goal not in ("overview",) and not _has_measure_match(intent.raw, profile):
        # Only skip if intent explicitly mentions a measure that doesn't map and also needs a numeric
        # For bar/comparison, we need a numeric; if none matches, skip
        if intent.raw.strip():
            # Ensure it's not a generic count request
            low = intent.raw.lower()
            if any(w in low for w in ["profit","margin"]) and "product" in low:
                return [ChartSpec(id=f"spec_{idx_offset+1}", sheet=sheet_name, chart_type="bar", title=intent.raw[:90], x=None, y=None, status="skipped", skip_reason=f"Could not map the measure term in '{intent.raw}' to any numeric column in the schema.")]
            if not _has_column_match(intent.raw, profile):
                return [ChartSpec(id=f"spec_{idx_offset+1}", sheet=sheet_name, chart_type="bar", title=intent.raw[:90], x=None, y=None, status="skipped", skip_reason=f"Could not map any term in '{intent.raw}' to a column in the schema.")]

    # Goal-driven generation
    if goal == "trend":
        x = _best_temporal(profile) or _best_categorical(profile, intent.raw, set(), 100)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if x and y:
            ctype = "area" if intent.raw and "area" in intent.raw.lower() else "line"
            specs.append(ChartSpec(id=f"spec_{idx_offset+1}_line", sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Trend of {y} over {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum"), data_notes=intent.raw if intent.wants_split else None))
        elif y:
            pass
    elif goal == "comparison" or goal == "ranking":
        x = _best_categorical(profile, intent.raw, set(), 100)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if x:
            ctype = "horizontal_bar" if goal == "ranking" or (profile.by_name(x) and profile.by_name(x).cardinality > 10) else "bar"
            if y:
                specs.append(ChartSpec(id=f"spec_{idx_offset+1}_bar", sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"{y} by {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum"), data_notes=("Top 10" if intent.wants_top_n else None)))
            else:
                specs.append(ChartSpec(id=f"spec_{idx_offset+1}_bar", sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Count by {x}", x=x, y=None, agg_function="count", data_notes=None))
    elif goal == "distribution":
        y = _best_numeric(profile, intent.raw, set())
        x = _best_categorical(profile, intent.raw, {y} if y else set(), 10)
        if y:
            specs.append(ChartSpec(id=f"spec_{idx_offset+1}_histogram", sheet=sheet_name, chart_type="histogram", title=intent.raw[:90] or f"Distribution of {y}", x=y, y=None, agg_function="count"))
            if x and profile.by_name(x).cardinality <= 10:
                specs.append(ChartSpec(id=f"spec_{idx_offset+1}_box", sheet=sheet_name, chart_type="boxplot", title=f"Distribution of {y} by {x}", x=x, y=y, agg_function=None))
    elif goal == "relationship":
        n1 = _best_numeric(profile, intent.raw, set())
        n2 = _best_numeric(profile, intent.raw, {n1} if n1 else set())
        if n1 and n2:
            specs.append(ChartSpec(id=f"spec_{idx_offset+1}_scatter", sheet=sheet_name, chart_type="scatter", title=intent.raw[:90] or f"{n1} vs {n2}", x=n1, y=n2))
    elif goal == "correlation":
        if len(profile.numeric_cols) >= 3:
            specs.append(ChartSpec(id=f"spec_{idx_offset+1}_heatmap", sheet=sheet_name, chart_type="heatmap", title=intent.raw[:90] or "Correlation heatmap", x=profile.numeric_cols[0], y=profile.numeric_cols[1]))
        else:
            n1 = _best_numeric(profile, intent.raw, set())
            n2 = _best_numeric(profile, intent.raw, {n1} if n1 else set())
            if n1 and n2:
                specs.append(ChartSpec(id=f"spec_{idx_offset+1}_scatter", sheet=sheet_name, chart_type="scatter", title=intent.raw[:90] or f"{n1} vs {n2}", x=n1, y=n2))
    elif goal == "composition":
        x = _best_categorical(profile, intent.raw, set(), 30)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if x:
            group = _best_categorical(profile, intent.raw, {x, y} if y else {x}, 6)
            if group and profile.by_name(group).cardinality <= 6:
                ctype = "stacked_100" if "100%" in intent.raw else "stacked_bar"
                specs.append(ChartSpec(id=f"spec_{idx_offset+1}_{ctype}", sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Composition of {y or 'count'} by {x} and {group}", x=x, y=y, group_by=group, agg_function=_pick_agg(intent, "sum")))
            elif y:
                if profile.by_name(x).cardinality <= 6:
                    ctype = "donut" if intent.raw and "donut" in intent.raw.lower() else "pie"
                    specs.append(ChartSpec(id=f"spec_{idx_offset+1}_{ctype}", sheet=sheet_name, chart_type=ctype, title=intent.raw[:90] or f"Share of {x}", x=x, y=y, agg_function=_pick_agg(intent, "sum")))
                else:
                    specs.append(ChartSpec(id=f"spec_{idx_offset+1}_stacked", sheet=sheet_name, chart_type="stacked_bar", title=intent.raw[:90] or f"Stacked {y} by {x}", x=x, y=y, group_by=group))
    elif goal == "overview":
        pass
    else:
        x = _best_categorical(profile, intent.raw, set(), 30)
        y = _best_numeric(profile, intent.raw, {x} if x else set())
        if x and y:
            specs.append(ChartSpec(id=f"spec_{idx_offset+1}_bar", sheet=sheet_name, chart_type="bar", title=intent.raw[:90] or f"{y} by {x}", x=x, y=y, agg_function="sum"))

    # Apply intent modifiers
    for s in specs:
        if intent.wants_top_n and "top" not in (s.data_notes or "").lower():
            s.data_notes = ((s.data_notes or "") + f" Top {intent.wants_top_n}").strip()
        if intent.wants_split and "split" not in (s.data_notes or "").lower():
            s.data_notes = ((s.data_notes or "") + " Split comma-separated values").strip()
        if intent.raw and len(intent.raw) < 90:
            s.title = intent.raw[:90]

    return specs

def generate_exploratory(profile: DataProfile, sheet_name: str) -> list[ChartSpec]:
    """First-class exploration: identify valuable analytical questions."""
    specs: list[ChartSpec] = []
    idx = 0

    # 1. Best categorical × numeric for comparison
    x = _best_categorical(profile, "", set(), 100)
    y = _best_numeric(profile, "", {x} if x else set())
    if x and y:
        ctype = "horizontal_bar" if profile.by_name(x).cardinality > 10 else "bar"
        specs.append(ChartSpec(id=f"exp_{idx+1}_bar", sheet=sheet_name, chart_type=ctype, title=f"{y} by {x}", x=x, y=y, agg_function="sum"))
        idx += 1

    # 2. Temporal × numeric for trend
    t = _best_temporal(profile)
    if t and y:
        y2 = y or _best_numeric(profile, "", set())
        if y2:
            specs.append(ChartSpec(id=f"exp_{idx+1}_line", sheet=sheet_name, chart_type="line", title=f"{y2} over {t}", x=t, y=y2, agg_function="sum"))
            idx += 1

    # 3. Numeric distribution
    if y:
        specs.append(ChartSpec(id=f"exp_{idx+1}_hist", sheet=sheet_name, chart_type="histogram", title=f"Distribution of {y}", x=y, y=None, agg_function="count"))
        idx += 1
        if x and profile.by_name(x).cardinality <= 8:
            specs.append(ChartSpec(id=f"exp_{idx+1}_box", sheet=sheet_name, chart_type="boxplot", title=f"Distribution of {y} by {x}", x=x, y=y))
            idx += 1

    # 4. Relationship: 2 numerics → scatter
    n1 = _best_numeric(profile, "", set())
    n2 = _best_numeric(profile, "", {n1} if n1 else set())
    if n1 and n2:
        n3 = _best_numeric(profile, "", {n1, n2})
        specs.append(ChartSpec(id=f"exp_{idx+1}_scatter", sheet=sheet_name, chart_type="scatter", title=f"{n1} vs {n2}", x=n1, y=n2))
        idx += 1
        if len(profile.numeric_cols) >= 3 and n3:
            specs.append(ChartSpec(id=f"exp_{idx+1}_heatmap", sheet=sheet_name, chart_type="heatmap", title="Correlation of numeric measures", x=n1, y=n2))
            idx += 1

    # 5. Composition: second categorical for grouped/stacked if exists
    second_cat = _best_categorical(profile, "", {x} if x else set(), 8)
    if x and second_cat and y and second_cat != x:
        specs.append(ChartSpec(id=f"exp_{idx+1}_grouped", sheet=sheet_name, chart_type="grouped_bar", title=f"{y} by {x} and {second_cat}", x=x, y=y, group_by=second_cat, agg_function="sum"))
        idx += 1

    # 6. Small-cat pie if appropriate (2-5 cats)
    if x and 2 <= profile.by_name(x).cardinality <= 5 and y:
        specs.append(ChartSpec(id=f"exp_{idx+1}_pie", sheet=sheet_name, chart_type="pie", title=f"Share of {x}", x=x, y=y, agg_function="sum"))
        idx += 1

    return specs
