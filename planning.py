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

from llm import LLMError, llm_structured
from schemas import ChartSpec, ChartSpecList, SheetProfile
from tools.rule_engine import apply_rules

PLAN_SYSTEM_PROMPT = (
    "You turn a data analyst's plain-English visualization guideline into structured chart "
    "specifications. You are given a schema summary and a list of guideline lines.\n\n"
    "CRITICAL: Multiple lines often describe DIFFERENT ASPECTS of the SAME chart. "
    "Analyze ALL lines together. When lines share the same topic, entities, or metrics, "
    "combine them into ONE ChartSpec that satisfies every requirement.\n\n"
    "Follow this reasoning for each distinct chart intent:\n"
    "1. Understand entities: what data subjects are mentioned?\n"
    "2. Business meaning: what question is being answered?\n"
    "3. Choose metric: what column is being measured? (sum, count, count-distinct, etc.)\n"
    "4. Choose dimension: what column is the categorical axis?\n"
    "5. Data quality: are there special handling needs (e.g. split comma-separated values, "
    "filter nulls, count distinct)? Put these in the data_notes field.\n"
    "6. Choose visualization: pick the best chart type. Use explicit instructions when given; "
    "otherwise infer (time -> line, category comparison -> bar, part-of-whole -> pie, "
    "numeric relationship -> scatter, ranking -> horizontal_bar).\n"
    "7. Sorting/ranking: apply sort order and top-N limits from the guideline.\n\n"
    "Match guideline terms to actual column names from the schema -- never invent columns. "
    "If you cannot map a term, set status='skipped' with a clear skip_reason."
)

# --- deterministic fallback matching ----------------------------------------

SYNONYMS: dict[str, set[str]] = {
    "revenue": {"revenue", "sales", "total", "gross", "income", "amount", "turnover", "receipt"},
    "sales": {"sales", "revenue", "total", "gross", "income", "amount", "turnover"},
    "units": {"units", "qty", "quantity", "count", "volume", "shipment"},
    "profit": {"profit", "margin", "earnings", "net", "gp", "pbt"},
    "margin": {"margin", "profit", "pct", "percentage", "rate"},
    "cost": {"cost", "cogs", "expense", "spend", "price", "purchase"},
    "date": {"date", "time", "year", "month", "day", "quarter", "week", "period", "timestamp"},
    "region": {"region", "country", "state", "city", "territory", "location", "area", "zone", "geography", "geo", "nation", "province", "district"},
    "product": {"product", "item", "sku", "sku_id", "category", "name", "title", "variant"},
    "customer": {"customer", "client", "buyer", "account", "user"},
}

CHART_KEYWORDS = {
    "line": {"line chart", "line graph", "trend", "over time", "time series", "progression", "line"},
    "horizontal_bar": {"horizontal bar", "horizontal", "rank", "ranking", "top "},
    "pie": {"pie chart", "pie", "donut", "share of", "distribution by", "breakdown by", "proportion", "slice"},
    "scatter": {"scatter", "correlation", "relationship", "vs ", "versus", "plot of", "scatter plot"},
    "bar": {"bar chart", "bar graph", "column chart", "comparison", "bar"},
    "map": {"map", "geographic", "geo", "choropleth", "heat map", "on map", "by country", "by region", "by state", "by city", "by geography"},
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
    for ctype in ("map", "horizontal_bar", "pie", "line", "scatter", "bar"):
        for kw in CHART_KEYWORDS[ctype]:
            if kw in norm:
                return ctype
    # No explicit keyword: date-like x -> line, geography-like x -> map, otherwise bar.
    if x_col and (_tokens(x_col) & SYNONYMS["date"]) or "trend" in line.lower() or "time" in line.lower():
        return "line"
    if x_col and (_tokens(x_col) & SYNONYMS["region"]):
        return "map"
    return "bar"


def _detect_agg(line: str) -> str:
    norm = _norm(line)
    for agg, kws in AGG_KEYWORDS.items():
        for kw in kws:
            if kw in norm:
                return agg
    return "sum"


def _find_measure_column(line: str, profile: SheetProfile, exclude: set[str]) -> str | None:
    """Best numeric column for the measure, excluding already-chosen ones."""
    best_col, best_score = None, 0.0
    for col in profile.columns:
        if col.name in exclude:
            continue
        if col.dtype not in ("int64", "int32", "float64", "float32", "Int64", "Float64"):
            continue
        score = _column_score(line, col.name) + _synonym_score(line, col.name)
        if score > best_score:
            best_col, best_score = col.name, score
    return best_col or _fallback_numeric(profile, exclude)


def _fallback_numeric(profile: SheetProfile, exclude: set[str]) -> str | None:
    for col in profile.columns:
        if col.name not in exclude and col.dtype in (
            "int64", "int32", "float64", "float32", "Int64", "Float64",
        ):
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


def _find_geography_column(profile: SheetProfile) -> str | None:
    """A column whose tokens look like a geography/location axis."""
    geo_syns = SYNONYMS["region"]
    best, best_score = None, 0.0
    for col in profile.columns:
        toks = _tokens(col.name)
        overlap = len(toks & geo_syns)
        if overlap and overlap > best_score:
            best, best_score = col.name, overlap
    return best


def _is_geography_column(col_name: str, profile: SheetProfile) -> bool:
    """Check if a column contains geographic data."""
    geo_syns = SYNONYMS["region"]
    # Check column name
    toks = _tokens(col_name)
    if toks & geo_syns:
        return True
    # Check sample values for country/state patterns
    for col in profile.columns:
        if col.name == col_name and col.sample_values:
            for sv in col.sample_values[:5]:
                sv_lower = str(sv).lower().strip()
                # Check for common country names
                common_countries = {
                    "usa", "us", "united states", "uk", "united kingdom", "canada",
                    "germany", "france", "china", "japan", "india", "brazil",
                    "australia", "mexico", "italy", "spain", "russia", "korea",
                    "nigeria", "egypt", "south africa", "kenya", "ethiopia",
                    "uae", "saudi arabia", "qatar", "kuwait", "bahrain", "oman",
                    "jordan", "lebanon", "iraq", "syria", "palestine", "yemen",
                    "libya", "tunisia", "morocco", "algeria", "sudan",
                }
                if sv_lower in common_countries:
                    return True
    return False


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
        if col.name in exclude or col.dtype not in NUMERIC_DTYPES:
            continue
        sc = _column_score(line, col.name) + _synonym_score(line, col.name)
        if sc > best_score:
            best, best_score = col.name, sc
    return best, best_score


def _best_category(line: str, prof: SheetProfile, exclude: set[str]) -> tuple[str | None, float]:
    best, best_score = None, 0.0
    for col in prof.columns:
        if col.name in exclude or col.dtype in NUMERIC_DTYPES:
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

    for idx, group in enumerate(groups, start=1):
        merged_line = _merge_group_to_line(group)
        data_notes = _detect_data_notes(group)
        title_line = group[0]

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
                    specs.append(
                        ChartSpec(
                            id=f"spec_{idx}", sheet=prof.sheet_name, chart_type=ctype,
                            title=re.sub(r"\s+", " ", title_line).strip()[:90], x=cat_col, y=None,
                            data_notes=data_notes,
                            status="skipped",
                            skip_reason=(
                                f"Could not map the measure term in '{title_line}' to any numeric "
                                "column in the schema; the closest category column is "
                                f"'{cat_col}'."
                            ),
                        )
                    )
                    continue
            else:
                x_col, y_col = cat_col, measure

        title = re.sub(r"\s+", " ", title_line).strip()[:90] or f"Chart {idx}"
        spec = ChartSpec(
            id=f"spec_{idx}",
            sheet=prof.sheet_name,
            chart_type=ctype,
            title=title,
            x=x_col,
            y=y_col,
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
    if spec.chart_type == "bar" and n_categories and 2 <= n_categories <= 8:
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


# --- LLM path ----------------------------------------------------------------

def _build_user_prompt(profiles: list[SheetProfile], lines: Sequence[str]) -> str:
    blocks = []
    for p in profiles:
        cols = []
        for c in p.columns:
            cols.append(
                f"- {c.name} (dtype {c.dtype}, {c.null_count} null, {c.unique_count} unique, "
                f"samples: {', '.join(c.sample_values[:3]) or 'n/a'})"
            )
        blocks.append(f"Sheet '{p.sheet_name}': {p.row_count} rows\n" + "\n".join(cols))
    schema_summary = "\n\n".join(blocks)
    lines_txt = "\n".join(f"{i+1}. {ln}" for i, ln in enumerate(lines))
    return (
        "Spreadsheet schema summary:\n"
        f"{schema_summary}\n\n"
        "Guideline lines (multiple lines may describe the same chart -- combine them into ONE "
        "spec when they share the same topic, entities, or metrics):\n"
        f"{lines_txt}\n\n"
        "Return a JSON object: {\"specs\": [<ChartSpec>, ...]}. "
        "Use exactly the column names listed above. Chart types: line, bar, horizontal_bar, pie, "
        "scatter, map. For skipped items set \"status\": \"skipped\" and explain skip_reason. "
        "Sets an appropriate agg_function (sum/mean/count/etc.). Do not reference an "
        "instructions/guideline sheet as data."
    )


def _llm_plan(profiles: list[SheetProfile], lines: Sequence[str]) -> list[ChartSpec]:
    user = _build_user_prompt(profiles, lines)
    try:
        out = llm_structured(PLAN_SYSTEM_PROMPT, user, ChartSpecList)
    except LLMError:
        raise
    return list(out.specs)


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

        fields = [("x", spec.x), ("y", spec.y), ("group_by", spec.group_by)]
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
        missing = [label for label, val in fields if val and val not in col_names]
        if missing:
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


def _is_instructions_sheet(name: str) -> bool:
    n = name.strip().lower().replace("_", " ").replace("-", " ")
    return "instruction" in n or "guideline" in n


def plan_charts(profiles: list[SheetProfile], lines: Sequence[str]) -> list[ChartSpec]:
    """End-to-end planning: LLM structured call (fallback deterministic, no key)."""
    data_profiles = [p for p in profiles if p.columns and not _is_instructions_sheet(p.sheet_name)]
    specs: list[ChartSpec] | None = None
    try:
        specs = _llm_plan(data_profiles, lines)
    except LLMError:
        specs = None
    if specs is None:
        specs = deterministic_plan(data_profiles, lines)
    return _ensure_valid_specs(specs, profiles)