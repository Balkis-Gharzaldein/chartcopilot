"""Planning step -- LLM call #1: guideline lines -> list[ChartSpec].

The LLM is only ever given SheetProfiles (never raw data).  Its structured
output is validated against the Pydantic schema (with one retry on failure).
A deterministic fallback plan is used when no API key is configured, and every
returned spec is post-validated so that a spec referencing a sheet or column
that does not exist is skipped with a reason -- never silently guessed.
"""

from __future__ import annotations

import re
from typing import Sequence

from schemas import ChartSpec, SheetProfile
from tools.rule_engine import apply_rules
from viz.resolve import looks_numeric_samples
from viz.resolve import score_query as _resolve_score

# --- deterministic matching ---------------------------------------------------

SYNONYMS: dict[str, set[str]] = {
    "revenue": {"revenue", "sales", "total", "gross", "income", "amount", "turnover", "receipt"},
    "sales": {"sales", "revenue", "total", "gross", "income", "amount", "turnover"},
    "units": {"units", "qty", "quantity", "count", "volume", "shipment"},
    "profit": {"profit", "margin", "earnings", "net", "gp", "pbt"},
    "margin": {"margin", "profit", "pct", "percentage", "rate"},
    "cost": {"cost", "cogs", "expense", "spend", "price", "purchase"},
    "date": {"date", "time", "year", "month", "day", "quarter", "week", "period", "timestamp"},
    "region": {"region", "country", "state", "city", "territory", "location", "area", "zone", "nation", "province", "district"},
    "geography": {"geography", "geo"},
    "product": {"product", "item", "sku", "sku_id", "category", "name", "title", "variant"},
    "customer": {"customer", "client", "buyer", "account", "user"},
}

CHART_KEYWORDS = {
    "line": {"line chart", "line graph", "trend", "over time", "time series", "progression", "line"},
    "horizontal_bar": {"horizontal bar", "horizontal", "rank", "ranking", "top "},
    "pie": {"pie chart", "pie", "donut", "share of", "distribution by", "breakdown by", "proportion", "slice"},
    "scatter": {"scatter", "correlation", "relationship", "vs ", "versus", "plot of", "scatter plot"},
    "bar": {"bar chart", "bar graph", "column chart", "comparison", "bar"},
}

AGG_KEYWORDS = {
    "mean": {"average", "mean", "avg"},
    "count": {"count", "number of", "how many", "frequency", "#"},
    "sum": {"total", "sum", "overall", "revenue", "sales", "units", "amount", "value", "profit"},
    "max": {"maximum", "highest", "largest", "top"},
    "min": {"minimum", "lowest"},
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.strip().lower())


def _tokens(name: str) -> set[str]:
    toks = set(_norm(name).split(" "))
    toks.discard("")
    return toks


def _word_terms(line: str) -> list[str]:
    """Lower-cased, whitespace-normalized key phrases and words in the line."""
    line = line.lower()
    phrases = [p.strip() for p in re.split(r"[.;,()]|\band\b|\bor\b|--|\u2014|\u2013", line) if p.strip()]
    words = [_norm(w) for w in re.findall(r"[a-zA-Z0-9_]+", line)]
    return phrases + words


def _column_score(line: str, col: str) -> float:
    """Score how well a guideline line maps to a column name (0..~3)."""
    col_norm = _norm(col)
    col_toks = _tokens(col)
    line_lower = line.lower()
    score = 0.0
    if col_norm and col_norm in line_lower:
        score += 3.0
    if col_norm and col_norm in (_norm(line_lower)):
        score += 2.0
    for col_tok in col_toks:
        if col_tok and col_tok in _norm(line_lower):
            score += 1.2
    return score


def _synonym_score(line: str, col: str) -> float:
    """Extra score when a known synonym term links the line to the column."""
    col_toks = _tokens(col)
    line_norm = _norm(line)
    best = 0.0
    for term, syns in SYNONYMS.items():
        if _norm(term) in line_norm:
            overlap = col_toks & syns
            if overlap:
                best = max(best, 1.0 + 0.25 * len(overlap))
    return best


def _pick_best_column(line: str, profile: SheetProfile) -> tuple[str, float] | None:
    best_col, best_score = None, 0.0
    for col in profile.columns:
        score = _column_score(line, col.name) + _synonym_score(line, col.name)
        if score > best_score:
            best_col, best_score = col.name, score
    return (best_col, best_score) if best_col else None


def _detect_chart_type(line: str, x_col: str | None) -> str:
    norm = _norm(line)
    for ctype in ("horizontal_bar", "pie", "line", "scatter", "bar"):
        for kw in CHART_KEYWORDS[ctype]:
            if kw in norm:
                return ctype
    # No explicit keyword: date-like x -> line, otherwise bar.
    if x_col and (_tokens(x_col) & SYNONYMS["date"]) or "trend" in line.lower() or "time" in line.lower():
        return "line"
    return "bar"


def _detect_agg(line: str) -> str:
    norm = _norm(line)
    for agg, kws in AGG_KEYWORDS.items():
        for kw in kws:
            if kw in norm:
                return agg
    return "sum"


_HOW_MANY_RE = re.compile(
    r"how\s+many\s+([A-Za-z0-9][A-Za-z0-9 _\-]*?)"
    r"(?:\s+did\b|\s+does\b|\s+do\b|\s+by\b|\s+for\b|\s+in\b|\s+per\b|\s+of\b|\?|$)",
    re.IGNORECASE,
)
_QUARTER_GUARD = re.compile(r"\bQ[1-4]\b", re.IGNORECASE)


def _union_measure_cols(data_profiles) -> list[str]:
    """Numeric-measure column names across sheets (sample-aware, not dtype-only)."""
    seen: list[str] = []
    for p in data_profiles or []:
        for c in p.columns:
            if c.name not in seen and _is_measure_col(c):
                seen.append(c.name)
    return seen


def _resolve_measure_for_text(text: str, data_profiles) -> str | None:
    """Best numeric measure for a clause, or None.

    Applies the "how many <noun>" rule first ("how many units" -> SUM(PCS),
    never COUNT(*)), then per-word resolver scoring over numeric candidates.
    """
    cols = _union_measure_cols(data_profiles)
    if not cols or not (text or "").strip():
        return None
    hm = _HOW_MANY_RE.search(text)
    candidates = [hm.group(1).strip()] if hm and hm.group(1).strip() else []
    # score both the how-many noun (when present) and the full clause
    texts = candidates + [text] if candidates else [text]
    best, best_score = None, 0.0
    for t in texts:
        for c in cols:
            s = _resolve_score(t, c)
            if s > best_score:
                best, best_score = c, s
    return best if best_score >= 1.0 else None


def _split_dual_clauses(group: list[str], data_profiles) -> dict | None:
    """Split one guideline group into two single-measure clauses.

    "Top 10 customers by revenue, AND how many units did each buy" carries
    two measures sharing one dimension + top-N context. The deterministic
    planner otherwise merges them into a single COUNT(*) chart.
    Returns None unless the text splits cleanly into exactly two parts that
    resolve to DIFFERENT numeric measures; then
    {"parts": [(clause, measure), ...], "top_n": int|None,
     "rank_measure": measure|None}.
    """
    text = " ".join(g.strip() for g in group if g and g.strip())
    if not text or " and " not in text.lower():
        return None
    parts = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    p1 = parts[0].strip().strip(" |,:;")
    p2 = parts[1].strip().strip(" |,:;")
    if len(p1) < 10 or len(p2) < 10:
        return None
    # "Q1 and Q4" style filter comparisons are not dual metrics.
    if _QUARTER_GUARD.search(p1) and _QUARTER_GUARD.search(p2):
        return None
    m1 = _resolve_measure_for_text(p1, data_profiles)
    m2 = _resolve_measure_for_text(p2, data_profiles)
    if not m1 or not m2 or m1 == m2:
        return None
    top_n, rank_measure = None, None
    for part, m in ((p1, m1), (p2, m2)):
        mt = _TOPN_PATTERN.search(part)
        if mt:
            top_n, rank_measure = int(mt.group(1)), m
            break
    return {"parts": [(p1, m1), (p2, m2)], "top_n": top_n, "rank_measure": rank_measure}


def _is_measure_col(col) -> bool:
    """A column usable as a numeric measure.

    Raw-dtype check alone hides text-stored measures (currency "$1,200",
    percents, comma-grouped numbers parsed as object) — the profiler already
    treats those as numeric via samples, so the planner must too.
    Bare row-id columns ("index") are never measures, even with int dtype.
    """
    _idx_like = {"index", "idx", "row_number", "rownumber", "level_0"}
    _nm = _norm(getattr(col, "name", ""))
    if _nm in _idx_like or _nm.startswith("unnamed"):
        return False
    if col.dtype in ("int64", "int32", "float64", "float32", "Int64", "Float64"):
        return True
    return looks_numeric_samples(getattr(col, "sample_values", None))


def _find_measure_column(line: str, profile: SheetProfile, exclude: set[str]) -> str | None:
    """Best numeric column for the measure, excluding already-chosen ones."""
    best_col, best_score = None, 0.0
    for col in profile.columns:
        if col.name in exclude:
            continue
        if not _is_measure_col(col):
            continue
        score = _resolve_score(line, col.name)
        if score > best_score:
            best_col, best_score = col.name, score
    return best_col or _fallback_numeric(profile, exclude)


def _fallback_numeric(profile: SheetProfile, exclude: set[str]) -> str | None:
    for col in profile.columns:
        if col.name not in exclude and _is_measure_col(col):
            return col.name
    return None


def _find_time_column(profile: SheetProfile) -> str | None:
    """A column whose tokens look like a date/time axis."""
    date_syns = SYNONYMS["date"]
    best, best_score = None, 0.0
    for col in profile.columns:
        toks = _tokens(col.name)
        overlap = len(toks & date_syns)
        if overlap and overlap > best_score:
            best, best_score = col.name, overlap
    return best


NUMERIC_DTYPES = {"int64", "int32", "float64", "float32", "Int64", "Float64"}

MEASURE_TERMS = set(SYNONYMS.keys()) | {
    "revenue", "sales", "sum", "total", "profit", "margin", "units", "qty",
    "quantity", "cost", "value", "amount", "price", "earnings", "gross",
}


def _is_measure_term(line: str) -> bool:
    ln = _norm(line)
    return any(_norm(term) in ln for term in MEASURE_TERMS)


def _best_numeric(line: str, prof: SheetProfile, exclude: set[str]) -> tuple[str | None, float]:
    best, best_score = None, 0.0
    for col in prof.columns:
        if col.name in exclude or not _is_measure_col(col):
            continue
        sc = _resolve_score(line, col.name)
        if sc > best_score:
            best, best_score = col.name, sc
    return best, best_score


def _best_category(line: str, prof: SheetProfile, exclude: set[str]) -> tuple[str | None, float]:
    best, best_score = None, 0.0
    for col in prof.columns:
        if col.name in exclude or _is_measure_col(col):
            continue
        sc = _column_score(line, col.name) + _synonym_score(line, col.name)
        if sc > best_score:
            best, best_score = col.name, sc
    return best, best_score


# --- line grouping: combine lines about the same chart into one intent --------

_SPLIT_KEYWORDS = {"split", "comma-separated", "comma separated", "explode", "each reason",
                    "each value", "separate label", "separate row", "separate entry"}
_TOPN_PATTERN = re.compile(r"top\s+(\d+)", re.IGNORECASE)
_SORT_DESC_KEYWORDS = {"highest to lowest", "descending", "rank", "sorted", "top ", "most frequent"}
_SORT_ASC_KEYWORDS = {"lowest to highest", "ascending", "least"}
_EXCLUDE_PATTERNS = [
    re.compile(r"(?:do not|don't|should not)\s+use\b", re.IGNORECASE),
    re.compile(r"\b(?:exclude|ignore)\b", re.IGNORECASE),
]
# Patterns to detect user-specified column names (backtick-quoted, backslash-escaped, or "the X column")
_EXPLICIT_COL_PATTERN = re.compile(r"[`'\"\\]([^`'\"\\]+)[`'\"\\]")
_THE_COL_PATTERN = re.compile(r"(?:the\s+|use\s+)([\w\s]+?)(?:\s+column|\s+as)", re.IGNORECASE)
# Patterns to detect explicit dimension/metric assignment
_DIM_PATTERNS = [
    re.compile(r"(?:use|as)\s+[`'\"\\]?(\w[\w\s]*\w)[`'\"\\]?\s+(?:as\s+)?(?:the\s+)?(?:categorical\s+)?(?:dimension|axis|x[\s-]axis|category|categories)", re.IGNORECASE),
    re.compile(r"[`'\"\\]?(\w[\w\s]*\w)[`'\"\\]?\s+(?:as\s+)?(?:the\s+)?(?:categorical\s+)?(?:dimension|axis|x[\s-]axis)", re.IGNORECASE),
]
_METRIC_PATTERNS = [
    re.compile(r"(?:count|sum|total|measure)\s+(?:unique\s+)?(?:values?\s+(?:of\s+)?)?[`'\"\\]?(\w[\w\s]*\w)[`'\"\\]?", re.IGNORECASE),
    re.compile(r"[`'\"\\]?(\w[\w\s]*\w)[`'\"\\]?\s+(?:as\s+)?(?:the\s+)?(?:metric|measure|count|value)", re.IGNORECASE),
    re.compile(r"(?:count|sum|total)\s+[`'\"\\]?(\w[\w\s]*\w)[`'\"\\]?", re.IGNORECASE),
]


def _find_excluded_columns(lines: Sequence[str], profiles: list[SheetProfile]) -> set[str]:
    """Detect 'do not use X' / 'exclude X' patterns and return column names to skip."""
    all_col_names = {col.name for prof in profiles for col in prof.columns}
    excluded: set[str] = set()
    for line in lines:
        line_l = line.strip().lower()
        if not line_l:
            continue
        is_negative = any(pat.search(line) for pat in _EXCLUDE_PATTERNS)
        if not is_negative:
            continue
        # Find all column names mentioned in this negative-instruction line
        for col_name in all_col_names:
            score = _column_score(line, col_name)
            if score > 0:
                excluded.add(col_name)
    return excluded


def _find_explicit_columns(
    lines: Sequence[str], profiles: list[SheetProfile],
) -> tuple[str | None, str | None]:
    """Detect user-explicitly-named dimension and metric columns.

    Looks for backtick-quoted names and phrases like "use X as the dimension",
    "count unique ID values", etc.  Returns (dimension_col, metric_col) — either
    may be None if not found.
    """
    all_col_names = {col.name for prof in profiles for col in prof.columns}
    combined = " ".join(lines).lower()

    dim_col: str | None = None
    metric_col: str | None = None

    # --- detect explicit dimension ---
    for pat in _DIM_PATTERNS:
        for m in pat.finditer(combined):
            candidate = m.group(1).strip()
            # Try exact match first
            if candidate in all_col_names:
                dim_col = candidate
                break
            # Try fuzzy match
            for col_name in all_col_names:
                if _column_score(candidate, col_name) > 2.0:
                    dim_col = col_name
                    break
            if dim_col:
                break
        if dim_col:
            break

    # --- detect explicit metric ---
    for pat in _METRIC_PATTERNS:
        for m in pat.finditer(combined):
            candidate = m.group(1).strip()
            if candidate in all_col_names:
                metric_col = candidate
                break
            for col_name in all_col_names:
                if _column_score(candidate, col_name) > 2.0:
                    metric_col = col_name
                    break
            if metric_col:
                break
        if metric_col:
            break

    # Fallback: find backtick-quoted column names not yet assigned
    if not dim_col or not metric_col:
        backtick_cols = [m.group(1) for m in _EXPLICIT_COL_PATTERN.finditer(" ".join(lines))]
        for bt in backtick_cols:
            if bt in all_col_names:
                if not dim_col:
                    dim_col = bt
                elif not metric_col and bt != dim_col:
                    metric_col = bt

    return dim_col, metric_col


def _detect_count_distinct(lines: Sequence[str]) -> bool:
    """Detect 'count unique', 'count distinct', 'unique count' patterns."""
    combined = " ".join(lines).lower()
    return bool(
        re.search(r"count\s+unique", combined)
        or re.search(r"count\s+distinct", combined)
        or re.search(r"unique\s+count", combined)
        or re.search(r"nunique", combined)
        or ("unique" in combined and "count" in combined)
    )


def _match_columns_for_line(
    line: str, profiles: list[SheetProfile], exclude: set[str] | None = None,
) -> set[str]:
    """Return the set of column names this guideline line matches (score > threshold)."""
    matched: set[str] = set()
    exclude = exclude or set()
    for prof in profiles:
        for col in prof.columns:
            if col.name in exclude:
                continue
            score = _column_score(line, col.name) + _synonym_score(line, col.name)
            if score > 1.5:
                matched.add(col.name)
    return matched


def _col_is_numeric(col_name: str, profiles: list[SheetProfile]) -> bool:
    for prof in profiles:
        for col in prof.columns:
            if col.name == col_name and col.dtype in NUMERIC_DTYPES:
                return True
    return False


def _is_high_cardinality_id(col_name: str, profiles: list[SheetProfile]) -> bool:
    """Check if a column is a high-cardinality identifier (like ID, URL, etc.)"""
    name_lower = col_name.lower().strip()
    id_names = {"id", "ids", "identifier", "key", "uuid", "url", "link", "code", "token"}
    if name_lower in id_names or name_lower.endswith("_id") or name_lower.endswith(" id"):
        return True
    for prof in profiles:
        for col in prof.columns:
            if col.name == col_name:
                if col.unique_count > 0 and prof.row_count > 0:
                    if col.unique_count / prof.row_count > 0.9:
                        return True
    return False


def _group_lines_by_intent(
    lines: Sequence[str], profiles: list[SheetProfile],
) -> tuple[list[list[str]], str | None]:
    """Group guideline lines that describe the same chart into clusters.

    Lines sharing the same top-matched column are grouped.  Lines with no column
    match (e.g. "use a bar chart") are absorbed into the largest group.
    Returns (groups, anchor_column_name).
    """
    non_empty = [ln.strip() for ln in lines if ln.strip()]
    if not non_empty:
        return [], None

    excluded = _find_excluded_columns(non_empty, profiles)
    line_col_sets = [_match_columns_for_line(ln, profiles, excluded) for ln in non_empty]

    # Count how many lines each column appears in (to find the anchor entity)
    col_counts: dict[str, int] = {}
    for matched in line_col_sets:
        for c in matched:
            col_counts[c] = col_counts.get(c, 0) + 1

    if not col_counts:
        return [non_empty], None

    # Prefer dimension (categorical) columns as anchor over numeric metric columns
    # Exclude high-cardinality identifiers (like ID, URL) from anchor consideration
    dim_counts = {c: n for c, n in col_counts.items()
                  if not _col_is_numeric(c, profiles) and not _is_high_cardinality_id(c, profiles)}
    num_counts = {c: n for c, n in col_counts.items()
                  if _col_is_numeric(c, profiles) and not _is_high_cardinality_id(c, profiles)}
    anchor_source = dim_counts if dim_counts else num_counts
    if not anchor_source:
        return [non_empty], None
    anchor = max(anchor_source, key=anchor_source.get)  # type: ignore[arg-type]

    chart_type_kws = {kw for kws in CHART_KEYWORDS.values() for kw in kws}

    anchor_group: list[str] = []
    other_groups: dict[str, list[str]] = {}
    for line, matched in zip(non_empty, line_col_sets):
        if anchor in matched:
            anchor_group.append(line)
        elif not matched:
            # No column match — check if it's a generic chart-type line that
            # should start its own group (e.g. "trend over time") or truly
            # generic and should be absorbed into the anchor group.
            line_lower = line.lower()
            has_chart_type = any(kw in line_lower for kw in chart_type_kws)
            if has_chart_type:
                # Start a generic group keyed by None (will become its own spec)
                other_groups.setdefault("__generic__", []).append(line)
            else:
                anchor_group.append(line)
        else:
            # Line matches columns OTHER than anchor — place in its own group
            # Prefer non-ID, non-numeric columns as the group key
            non_id_numeric = {c for c in matched
                              if not _is_high_cardinality_id(c, profiles)}
            non_id = {c for c in non_id_numeric
                      if not _col_is_numeric(c, profiles)}
            if non_id:
                best = max(non_id, key=lambda c: col_counts.get(c, 0))
            elif non_id_numeric:
                # All non-ID columns are numeric — use them
                best = max(non_id_numeric, key=lambda c: col_counts.get(c, 0))
            else:
                best = max(matched, key=lambda c: col_counts.get(c, 0))
            other_groups.setdefault(best, []).append(line)

    result = [anchor_group] if anchor_group else []
    result.extend(other_groups.values())

    # Merge generic lines (no column match) into matching groups by chart type.
    # e.g. "trend over time" (line chart keyword) should merge with a Date Posted group.
    if "__generic__" in other_groups:
        generic_lines = other_groups.pop("__generic__")
        # Rebuild result without generics
        result = [anchor_group] if anchor_group else []
        result.extend(other_groups.values())

        for gline in generic_lines:
            gline_lower = gline.lower()
            # Detect what chart type this generic line wants
            gline_ctype = None
            for ctype, kws in CHART_KEYWORDS.items():
                for kw in kws:
                    if kw in gline_lower:
                        gline_ctype = ctype
                        break
                if gline_ctype:
                    break

            # Try to find an existing group whose merged line suggests the same chart type
            merged = None
            for grp in result:
                combined = " ".join(grp).lower()
                for ctype, kws in CHART_KEYWORDS.items():
                    if ctype == gline_ctype:
                        for kw in kws:
                            if kw in combined:
                                merged = grp
                                break
                    if merged:
                        break
                if merged:
                    break

            if merged is not None:
                merged.append(gline)
            else:
                # No matching group — start a new group
                result.append([gline])

    return result, anchor


def _merge_group_to_line(group: list[str]) -> str:
    """Merge a group of guideline lines into a single combined line for spec generation."""
    return " | ".join(group)


def _detect_data_notes(group: list[str]) -> str | None:
    """Extract data transformation hints from the group of lines."""
    notes: list[str] = []
    combined = " ".join(group).lower()
    for kw in _SPLIT_KEYWORDS:
        if kw in combined:
            notes.append("Split comma-separated values into separate rows before aggregation.")
            break
    # Detect count-distinct intent
    if "count distinct" in combined or ("unique" in combined and "count" in combined):
        notes.append("Count distinct values (use nunique), not raw rows.")
    # Detect top-N
    m = _TOPN_PATTERN.search(combined)
    if m:
        notes.append(f"Show top {m.group(1)} results only.")
    # Detect sort order
    if any(kw in combined for kw in _SORT_DESC_KEYWORDS):
        notes.append("Sort descending (highest to lowest).")
    elif any(kw in combined for kw in _SORT_ASC_KEYWORDS):
        notes.append("Sort ascending (lowest to highest).")
    return " ".join(notes) if notes else None


def deterministic_plan(profiles: list[SheetProfile], lines: Sequence[str]) -> list[ChartSpec]:
    specs: list[ChartSpec] = []
    data_profiles = [p for p in profiles if p.columns]

    groups, anchor = _group_lines_by_intent(lines, data_profiles)

    # Expand dual-metric questions ("top 10 by revenue, AND how many units")
    # into two single-measure groups sharing one top-N context, so each
    # measure becomes its own chart instead of collapsing to COUNT(*).
    expanded: list[tuple[list[str], dict | None]] = []
    for grp in groups:
        split = _split_dual_clauses(grp, data_profiles)
        if split:
            for clause, measure in split["parts"]:
                expanded.append(([clause], {
                    "measure": measure,
                    "top_n": split["top_n"],
                    "rank_measure": split["rank_measure"],
                    "title": re.sub(r"\s+", " ", clause).strip()[:90],
                }))
        else:
            expanded.append((grp, None))

    for idx, (group, dual_ctx) in enumerate(expanded, start=1):
        merged_line = _merge_group_to_line(group)
        data_notes = _detect_data_notes(group)
        title_line = (dual_ctx or {}).get("title") or group[0]

        # Detect user-explicitly-named columns PER GROUP (not globally)
        explicit_dim, explicit_metric = _find_explicit_columns(group, data_profiles)
        wants_count_distinct = _detect_count_distinct(group)

        # Pick the sheet whose columns best match this group.
        best_prof, best_prof_score = None, 0.0
        for prof in data_profiles:
            cat, cat_sc = _best_category(merged_line, prof, set())
            num, num_sc = _best_numeric(merged_line, prof, set())
            score = (cat_sc if cat else 0) + (num_sc if num else 0)
            if score > best_prof_score:
                best_prof, best_prof_score = prof, score
        if best_prof is None or best_prof_score <= 0:
            specs.append(
                ChartSpec(
                    id=f"spec_{idx}",
                    sheet=data_profiles[0].sheet_name if data_profiles else "?",
                    chart_type="bar",
                    title=re.sub(r"\s+", " ", title_line).strip()[:90],
                    x=None,
                    y=None,
                    data_notes=data_notes,
                    status="skipped",
                    skip_reason="Could not map any term in this guideline to a column in the schema.",
                )
            )
            continue
        prof = best_prof

        ctype = _detect_chart_type(merged_line, None)

        # --- Fix 1: User-specified columns take priority over scoring ---
        if explicit_dim and explicit_dim in [c.name for c in prof.columns]:
            cat_col = explicit_dim
        else:
            cat_col = anchor if (anchor and anchor in [c.name for c in prof.columns]) else None
            if not cat_col:
                cat_col, _ = _best_category(merged_line, prof, set())

        # --- Fix 2: Detect count_distinct properly ---
        measure = None
        if wants_count_distinct:
            agg = "count_distinct"
            # User-specified metric takes priority
            if explicit_metric and explicit_metric in [c.name for c in prof.columns]:
                measure = explicit_metric
            else:
                # Find the identifier/metric column (any non-dimension column matching the line)
                for col in prof.columns:
                    if col.name != cat_col and _column_score(merged_line, col.name) > 0:
                        measure = col.name
                        break
        else:
            agg = _detect_agg(merged_line)
            measure, _ = _best_numeric(merged_line, prof, set())
            # "how many <noun>" with a resolvable numeric measure means
            # SUM(metric) ("how many units" -> SUM(PCS)), never COUNT(*).
            # Bare "how many" (no measure) keeps agg=count.
            if agg == "count":
                hm = _resolve_measure_for_text(merged_line, [prof])
                if hm:
                    measure, agg = hm, "sum"

        # Dual-metric clause: its own resolved measure wins (backtick-explicit
        # columns still take priority), so "…by revenue, and how many units…"
        # yields one chart per measure instead of one COUNT(*) chart.
        if (dual_ctx and not explicit_metric
                and dual_ctx.get("measure") in [c.name for c in prof.columns]):
            measure = dual_ctx["measure"]
            if _HOW_MANY_RE.search(merged_line):
                agg = "sum"

        x_col, y_col = None, None

        if ctype == "scatter":
            second, _ = _best_numeric(merged_line, prof, {measure} if measure else set())
            if measure and second:
                x_col, y_col = measure, second
            else:
                specs.append(
                    ChartSpec(
                        id=f"spec_{idx}", sheet=prof.sheet_name, chart_type="scatter",
                        title=re.sub(r"\s+", " ", title_line).strip()[:90], x=None, y=None,
                        data_notes=data_notes,
                        status="skipped",
                        skip_reason="Scatter chart requires two numeric columns; only "
                                    f"{(measure or '?')!r} could be matched.",
                    )
                )
                continue

        elif ctype == "line":
            time_col = _find_time_column(prof)
            if time_col:
                x_col = time_col
                y_col = measure
            else:
                x_col = cat_col
                y_col = measure
            if not y_col:
                specs.append(
                    ChartSpec(
                        id=f"spec_{idx}", sheet=prof.sheet_name, chart_type="line",
                        title=re.sub(r"\s+", " ", title_line).strip()[:90], x=x_col, y=None,
                        data_notes=data_notes,
                        status="skipped",
                        skip_reason="No numeric column could be matched as the measure for "
                                    f"{x_col!r}.",
                    )
                )
                continue

        else:  # bar / horizontal_bar / pie need a category + a measure
            if not cat_col:
                specs.append(
                    ChartSpec(
                        id=f"spec_{idx}", sheet=prof.sheet_name, chart_type=ctype,
                        title=re.sub(r"\s+", " ", title_line).strip()[:90], x=None, y=None,
                        data_notes=data_notes,
                        status="skipped",
                        skip_reason="No categorical column could be matched for this chart.",
                    )
                )
                continue
            if not measure:
                if agg in ("count", "count_distinct") or not _is_measure_term(merged_line):
                    if agg != "count_distinct":
                        agg = "count"
                    x_col, y_col = cat_col, None
                else:
                    _avail = ", ".join(c.name for c in prof.columns)
                    specs.append(
                        ChartSpec(
                            id=f"spec_{idx}", sheet=prof.sheet_name, chart_type=ctype,
                            title=re.sub(r"\s+", " ", title_line).strip()[:90], x=cat_col, y=None,
                            data_notes=data_notes,
                            status="skipped",
                            skip_reason=(
                                f"Could not map the measure term in '{title_line}' to any numeric "
                                "column in the schema; the closest category column is "
                                f"'{cat_col}'. Available columns: {_avail}."
                            ),
                        )
                    )
                    continue
            else:
                x_col, y_col = cat_col, measure

        # Shared top-N context for dual-metric clauses: both charts rank by the
        # same metric ("top 10 by revenue"), so the PCS chart shows the units
        # of the top-revenue customers instead of the top-by-PCS customers.
        # The agent's Top-N pushdown reads the data_notes marker + filter.
        dual_filter = None
        if (dual_ctx and dual_ctx.get("top_n") and dual_ctx.get("rank_measure")
                and dual_ctx["rank_measure"] in [c.name for c in prof.columns]
                and x_col):
            n = dual_ctx["top_n"]
            rank = dual_ctx["rank_measure"]
            marker = f" Top {n} by SUM({rank})."
            if marker.strip().lower() not in (data_notes or "").lower():
                data_notes = ((data_notes or "") + marker).strip()
            dual_filter = f"{x_col} in_top_n {n} rank_by SUM({rank})"

        title = re.sub(r"\s+", " ", title_line).strip()[:90] or f"Chart {idx}"
        spec = ChartSpec(
            id=f"spec_{idx}",
            sheet=prof.sheet_name,
            chart_type=ctype,
            title=title,
            x=x_col,
            y=y_col,
            filter=dual_filter,
            agg_function=agg,
            data_notes=data_notes,
            status="planned",
        )
        # Apply data-to-viz rules (long labels, many categories, time axis, etc.)
        rule_result = apply_rules(spec, prof)
        specs.append(rule_result.spec)
    return specs


# --- recommendation engine ---------------------------------------------------

def _count_categories_for_recommend(x_col: str | None, profile: SheetProfile) -> int | None:
    """Count distinct values in x column for recommendation logic."""
    if not x_col:
        return None
    for col in profile.columns:
        if col.name == x_col:
            return col.unique_count if col.unique_count > 0 else None
    return None


def recommend_charts(spec: ChartSpec, profile: SheetProfile) -> list[ChartSpec]:
    """From one spec, generate primary + recommended charts.

    Recommendations are alternative views the user might find useful:
    - Bar with few categories → suggest pie (part-of-whole view)
    - Bar with many categories → suggest horizontal_bar (ranking view)
    - Pie → suggest bar (better for comparison)
    - Line with single series → suggest bar (category comparison)

    Returns a list where the first element is always the original spec.
    """
    recommendations: list[ChartSpec] = [spec]
    n_categories = _count_categories_for_recommend(spec.x, profile)

    # Rule 1: Bar with 2-8 categories → suggest pie
    if spec.chart_type == "bar" and n_categories and 2 <= n_categories <= 6 and (spec.agg_function or "sum") in ("sum", "count") and "derived metric:" not in (spec.data_notes or "").lower():
        rec = spec.model_copy(deep=True)
        rec.chart_type = "pie"
        rec.title = f"Share of {spec.x}" if spec.x else f"{spec.title} (pie)"
        rec.id = spec.id + "_rec_pie"
        recommendations.append(rec)

    # Rule 2: Bar with >10 categories → suggest horizontal_bar
    if spec.chart_type == "bar" and n_categories and n_categories > 10:
        rec = spec.model_copy(deep=True)
        rec.chart_type = "horizontal_bar"
        rec.title = f"Rank {spec.x}" if spec.x else f"{spec.title} (ranked)"
        rec.id = spec.id + "_rec_hbar"
        recommendations.append(rec)

    # Rule 3: Pie → suggest bar (better for comparison)
    if spec.chart_type == "pie":
        rec = spec.model_copy(deep=True)
        rec.chart_type = "bar"
        rec.title = f"Compare by {spec.x}" if spec.x else f"{spec.title} (bar)"
        rec.id = spec.id + "_rec_bar"
        recommendations.append(rec)

    # Rule 4: Horizontal bar → suggest lollipop (cleaner alternative)
    if spec.chart_type == "horizontal_bar" and n_categories and n_categories > 5:
        rec = spec.model_copy(deep=True)
        rec.title = f"{spec.title} (lollipop)"
        rec.id = spec.id + "_rec_lollipop"
        # Lollipop is rendered as horizontal_bar with a flag
        rec.data_notes = (rec.data_notes or "") + " Render as lollipop chart."
        recommendations.append(rec)

    return recommendations


# --- LLM-direct planning removed (Option A contract) ---------------------------
# A structured LLM plan could name plausible-but-wrong existing columns
# (DATE for CUSTOMER) that post-validation cannot catch. Planning is
# deterministic-only; the LLM remains advisory for intent goals
# (viz/intent.py) and narrative (narrative.py).


# --- post-validation (applies to both paths) ---------------------------------

def _ensure_valid_specs(specs: list[ChartSpec], profiles: list[SheetProfile]) -> list[ChartSpec]:
    by_name = {p.sheet_name: p for p in profiles}
    instruction_sheets = [
        p.sheet_name for p in profiles
        if "instruction" in p.sheet_name.lower() or "guideline" in p.sheet_name.lower()
    ]
    out: list[ChartSpec] = []
    used_ids: set[str] = set()
    for i, spec in enumerate(specs, start=1):
        spec.id = spec.id.strip() if spec.id and spec.id.strip() else f"spec_{i}"
        if spec.id in used_ids:
            spec.id = f"spec_{i}"
        used_ids.add(spec.id)

        # Validate against the normalized spec below. Keep this list close to
        # the missing-column check so group_by normalization cannot leave it
        # stale.
        if spec.status != "planned":
            out.append(spec)
            continue

        if spec.sheet not in by_name:
            spec.status = "skipped"
            spec.skip_reason = f"Sheet '{spec.sheet}' does not exist in the uploaded file."
            out.append(spec)
            continue
        if spec.sheet in instruction_sheets:
            spec.status = "skipped"
            spec.skip_reason = f"Sheet '{spec.sheet}' is the instructions sheet, not a data sheet."
            out.append(spec)
            continue

        col_names = [c.name for c in by_name[spec.sheet].columns]
        # Normalize group_by coherence before missing check (fix orchestrator hallucinated group_by)
        GROUP_AWARE = {"grouped_bar", "stacked_bar", "stacked_100", "line", "area", "scatter", "heatmap", "boxplot"}
        if spec.group_by and spec.group_by in (spec.x, spec.y):
            spec.group_by = None
        if spec.chart_type in ("bar", "horizontal_bar", "pie", "donut", "histogram") and spec.group_by is not None:
            spec.group_by = None
        if spec.group_by and spec.group_by not in col_names:
            # Ignore invalid group_by gracefully for non-grouped charts, else skip
            if spec.chart_type in GROUP_AWARE:
                spec.status = "skipped"
                spec.skip_reason = f"Referenced column(s) not found in sheet '{spec.sheet}': group_by."
                out.append(spec)
                continue
            else:
                spec.group_by = None
        fields = [("x", spec.x), ("y", spec.y), ("group_by", spec.group_by)]
        derived_y = bool(spec.data_notes and "derived metric:" in spec.data_notes.lower())
        missing = [
            label for label, val in fields
            if val and val not in col_names and not (label == "y" and derived_y)
        ]
        if missing:
            # For group_by only, clear it instead of skipping if not required
            if missing == ["group_by"] and spec.chart_type not in GROUP_AWARE:
                spec.group_by = None
            else:
                spec.status = "skipped"
                spec.skip_reason = f"Referenced column(s) not found in sheet '{spec.sheet}': {', '.join(missing)}."
                out.append(spec)
                continue
        # Charts without an x-axis are not meaningful.
        if spec.chart_type == "pie" and not spec.y and spec.agg_function != "count":
            spec.y = col_names[0]
        elif spec.chart_type == "scatter" and (not spec.x or not spec.y):
            spec.status = "skipped"
            spec.skip_reason = "Scatter chart requires both an x and a y column."
            out.append(spec)
            continue
        out.append(spec)
    return out


def _clarification_for_spec(spec: ChartSpec, question: str, profile: SheetProfile) -> str | None:
    """Return a clarification when the selected field is genuinely ambiguous."""
    if spec.status != "planned" or not question.strip():
        return None
    low = question.lower()
    if any(phrase in low for phrase in ("analyze this data", "explore this", "useful visualizations", "overview")):
        return None

    numeric = [
        c.name for c in profile.columns
        if c.dtype.lower() in {"int64", "int32", "int16", "float64", "float32", "float16", "int", "float"}
        or looks_numeric_samples(c.sample_values)
    ]
    categorical = [
        c.name for c in profile.columns
        if c.dtype.lower() in ("object", "string", "category")
        and not looks_numeric_samples(c.sample_values)
    ]
    ranked_numeric = sorted(
        (( _resolve_score(question, name), name) for name in numeric), reverse=True
    )
    ranked_categorical = sorted(
        (( _resolve_score(question, name), name) for name in categorical), reverse=True
    )
    x_score = _resolve_score(question, spec.x) if spec.x else 0.0
    y_score = _resolve_score(question, spec.y) if spec.y else 0.0
    evidence = min(x_score, y_score) if spec.y else x_score
    spec.confidence = 0.95 if evidence >= 6.0 else 0.85 if evidence >= 3.0 else 0.6
    spec.uncertain = spec.confidence < 0.8

    def close_matches(ranked: list[tuple[float, str]]) -> list[str]:
        strong = [(score, name) for score, name in ranked if score >= 3.0]
        if len(strong) >= 2 and strong[0][0] - strong[1][0] < 1.5:
            return [name for _, name in strong[:3]]
        return []

    metric_options = close_matches(ranked_numeric)
    if metric_options and spec.y in metric_options and y_score < 6.0:
        options = ", ".join(f"`{name}`" for name in metric_options)
        return f"Which measure should I use for this chart: {options}?"

    dimension_options = close_matches(ranked_categorical)
    if (
        spec.chart_type not in {"scatter", "heatmap"}
        and x_score < 6.0
        and not any(token in low for token in ("over time", "monthly", "by month", "quarterly", "by quarter", "yearly", "by year"))
        and dimension_options
        and spec.x in dimension_options
    ):
        options = ", ".join(f"`{name}`" for name in dimension_options)
        return f"Which grouping column should I use: {options}?"

    has_measure_word = any(
        word in low for word in ("revenue", "sales", "amount", "value", "quantity", "units", "price", "profit", "average", "mean", "count")
    )
    if (
        spec.y in numeric
        and len(numeric) > 1
        and y_score < 6.0
        and not has_measure_word
        and "over time" in low
    ):
        options = ", ".join(f"`{name}`" for name in numeric[:4])
        return f"Which numeric measure should I use: {options}?"

    return None


def _apply_clarifications(specs: list[ChartSpec], lines: Sequence[str], profiles: list[SheetProfile]) -> list[ChartSpec]:
    """Mark ambiguous plans as skipped so they cannot silently execute."""
    # Multiple specs are intentional dual-metric requests; each metric has
    # already been resolved independently by the planner.
    if len(lines) != 1 or len(specs) > 1:
        return specs
    question = lines[0]
    by_name = {p.sheet_name: p for p in profiles}
    for spec in specs:
        profile = by_name.get(spec.sheet)
        if not profile:
            continue
        clarification = _clarification_for_spec(spec, question, profile)
        if clarification:
            spec.confidence = 0.5
            spec.uncertain = True
            spec.clarification = clarification
            spec.status = "skipped"
            spec.skip_reason = f"Clarification needed: {clarification}"
    return specs


def _is_instructions_sheet(name: str) -> bool:
    n = name.strip().lower().replace("_", " ").replace("-", " ")
    return "instruction" in n or "guideline" in n


def plan_charts(profiles: list[SheetProfile], lines: Sequence[str], frames: dict | None = None) -> list[ChartSpec]:
    """End-to-end planning (Option A contract): deterministic-first.

    The orchestrator resolves columns/chart shape through deterministic
    machinery (single resolver, gates, scoring); the LLM is advisory-only
    (intent goals, narrative) and never supplies column names. There is no
    LLM-direct plan fallback: a question the deterministic layers reject is
    returned as an explained skip, never an invented chart. Up to 2 charts
    per request (dual-And); anything more is rejected transparently.
    """
    data_profiles = [p for p in profiles if p.columns and not _is_instructions_sheet(p.sheet_name)]
    # Enforce one-request-at-a-time: if caller sent one question, keep exactly one line (do not split into multiple intents/charts)
    # The intent layer also enforces this, but keep lines intact here.
    if len(lines) == 1:
        line = lines[0].strip()
        if line:
            lines = [line]

    # Primary: Viz Intelligence orchestrator which now does LLM-first intent extraction → data-driven scoring → k=1 selection
    # This satisfies: LLM for understanding + deterministic fallback, data-driven chart selection
    try:
        from viz.orchestrator import orchestrate
        specs = orchestrate(profiles, frames, list(lines))
        # If orchestrator returns valid planned specs (single optimal), return them
        if specs and any(s.status == "planned" for s in specs):
            return _apply_clarifications(_ensure_valid_specs(specs, profiles), lines, profiles)
        # Orchestrator rejected everything: return the explained skips as-is.
        # No LLM-direct fallback by contract (it invents columns).
        if specs and all(s.status == "skipped" for s in specs):
            return _apply_clarifications(_ensure_valid_specs(specs, profiles), lines, profiles)
        if specs:
            return _apply_clarifications(_ensure_valid_specs(specs, profiles), lines, profiles)
        # Empty → fallback
    except Exception:
        pass

    # Fallback: deterministic keyword-lightweight plan (synonyms kept lightweight)
    specs = deterministic_plan(data_profiles, lines)
    # Ask mode with several planned deterministic candidates: keep the best
    # TWO distinct ones (dual-And needs 2; anything more is trimmed).
    if len(lines) == 1 and len([s for s in specs if s.status == "planned"]) > 1:
        # Use scoring to pick best among deterministic candidates
        try:
            from viz.profiler import profile_data
            from viz.intent import parse_intents
            from viz.scoring import score_candidate
            from viz.gates.can import can_gate
            from viz.gates.appropriate import appropriate_gate
            from viz.gates.useful import useful_gate
            intents = parse_intents(list(lines), profiles)
            goal = intents[0].goal if intents else "comparison"
            # Score deterministically generated specs
            for prof in data_profiles:
                df = frames.get(prof.sheet_name) if frames else None
                dprof = profile_data(prof, df)
                scored = []
                for s in specs:
                    if s.status != "planned":
                        continue
                    if not can_gate(s, dprof).passed:
                        continue
                    if not appropriate_gate(s, dprof).passed:
                        continue
                    if not useful_gate(s, dprof, goal).passed:
                        continue
                    sc, _, _ = score_candidate(s, dprof, goal, False)
                    scored.append((sc, s))
                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    # Keep the best TWO distinct specs (dual-And needs both;
                    # extra accidental multiples are trimmed).
                    kept: list = []
                    seen: set = set()
                    for _, s in scored:
                        key = (s.chart_type, s.x, s.y, s.group_by)
                        if key not in seen:
                            seen.add(key)
                            kept.append(s)
                        if len(kept) >= 2:
                            break
                    # Return kept + any skipped for transparency
                    skipped = [s for s in specs if s.status == "skipped"]
                    specs = kept + skipped
                    break
        except Exception:
            # Keep first planned specs if scoring fails (up to 2 for duals)
            planned = [s for s in specs if s.status == "planned"]
            skipped = [s for s in specs if s.status == "skipped"]
            specs = (planned[:2] + skipped) if planned else specs
    return _apply_clarifications(_ensure_valid_specs(specs, profiles), lines, profiles)
