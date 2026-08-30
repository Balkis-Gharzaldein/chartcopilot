"""Intent detection — user intent (A) vs exploratory fallback (B).

Parses natural language guideline lines into AnalyticalIntent objects.
Respects explicit chart-type requests; otherwise infers goal.
Broad/empty input → overview exploratory intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from schemas import SheetProfile

# Broad exploration phrases → overview intent
EXPLORATORY_PHRASES = {
    "analyze this data", "analyse this data", "explore this dataset", "explore this data",
    "show me useful visualizations", "show useful visualizations", "useful visualizations",
    "what are the most useful visualizations", "overview", "summarize this data",
    "show me insights", "explore the data",
}

GOAL_KEYWORDS: dict[str, set[str]] = {
    "trend": {"trend", "over time", "time series", "evolution", "progression", "change over", "timeline"},
    "relationship": {"relationship", "scatter", "association", "bubble"},
    "correlation": {"correlation", "correlogram", "heatmap", "matrix"},
    "composition": {"composition", "share", "proportion", "breakdown", "part-to-whole", "stacked", "share of"},
    "distribution": {"distribution", "histogram", "spread", "range", "variation", "dispersion"},
    "ranking": {"rank", "ranking", "top", "highest", "lowest", "sorted", "largest", "smallest", "leaderboard"},
    "comparison": {"compare", "comparison", "contrast", "difference", "between", "vs", "versus"},
}

CHART_TYPE_KEYWORDS: dict[str, set[str]] = {
    "bar": {"bar chart", "bar graph", "column chart"},
    "horizontal_bar": {"horizontal bar", "horizontal"},
    "grouped_bar": {"grouped bar", "grouped"},
    "stacked_bar": {"stacked bar", "stacked"},
    "stacked_100": {"100% stacked", "100% stacked bar", "percent stacked", "normalized stacked"},
    "line": {"line chart", "line graph", "line plot"},
    "area": {"area chart", "area plot", "stacked area"},
    "scatter": {"scatter plot", "scatter chart", "scatter"},
    "histogram": {"histogram"},
    "boxplot": {"box plot", "boxplot", "box chart"},
    "heatmap": {"heatmap", "heat map", "correlation heatmap"},
    "pie": {"pie chart", "pie"},
    "donut": {"donut", "doughnut"},
}

AGG_KEYWORDS: dict[str, set[str]] = {
    "count": {"count", "number of", "frequency", "how many"},
    "count_distinct": {"count distinct", "distinct count", "unique count", "count unique"},
    "sum": {"sum", "total", "overall"},
    "mean": {"average", "mean", "avg"},
    "median": {"median"},
    "max": {"maximum", "max"},
    "min": {"minimum", "min"},
}


@dataclass
class AnalyticalIntent:
    raw: str  # original line(s) joined
    goal: str  # comparison|ranking|trend|distribution|relationship|composition|correlation|overview
    explicit_chart_type: str | None = None
    explicit_agg: str | None = None
    wants_top_n: int | None = None
    wants_split: bool = False
    is_exploratory: bool = False  # true for broad overview
    group_lines: list[str] | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def _contains(phrase: str, text: str) -> bool:
    return phrase in text.lower()

def _detect_goal(text: str) -> str:
    low = text.lower()
    # Explicit chart-type implies goal
    if any(kw in low for kw in CHART_TYPE_KEYWORDS.get("line", set())) or "over time" in low or "trend" in low:
        return "trend"
    if "scatter" in low or (" vs " in low and "correlation" in low):
        return "relationship"
    if "histogram" in low or "distribution" in low:
        return "distribution"
    if "box plot" in low or "boxplot" in low:
        return "distribution"
    if "heatmap" in low or "correlation" in low:
        return "correlation"
    if "pie" in low or "donut" in low or "share of" in low or "part-to-whole" in low:
        return "composition"
    if "stacked" in low:
        return "composition"
    # Keyword goals
    for goal, kws in GOAL_KEYWORDS.items():
        for kw in kws:
            if kw in low:
                return goal
    return "comparison"  # default

def _detect_chart_type(text: str) -> str | None:
    low = text.lower()
    # Order matters: more specific first
    for ctype in ["grouped_bar", "stacked_100", "stacked_bar", "horizontal_bar", "heatmap", "histogram", "boxplot", "area", "donut", "pie", "scatter", "line", "bar"]:
        for kw in CHART_TYPE_KEYWORDS.get(ctype, set()):
            if kw in low:
                return ctype
    return None

def _detect_agg(text: str) -> str | None:
    low = text.lower()
    for agg, kws in AGG_KEYWORDS.items():
        for kw in kws:
            if kw in low:
                return agg
    return None

def _is_exploratory(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    for ph in EXPLORATORY_PHRASES:
        if ph in low:
            return True
    # Very short generic without column refs
    if low in {"analyze", "explore", "overview", "show me charts"}:
        return True
    return False

TOP_N_RE = re.compile(r"top\s+(\d+)", re.IGNORECASE)


def parse_intents(lines: list[str], sheet_profiles: list[SheetProfile] | None = None) -> list[AnalyticalIntent]:
    """Group lines into intents and detect goal/type per group.

    Reuses line grouping logic from planning but goal-aware.
    For Phase 3, we keep grouping simple: exploratory → single overview intent;
    otherwise each non-empty line is its own intent (later candidate generation will deduplicate).
    """
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    if not cleaned:
        return [AnalyticalIntent(raw="", goal="overview", is_exploratory=True, group_lines=[])]

    # If single broad phrase → overview
    joined = " ".join(cleaned)
    if _is_exploratory(joined) and len(cleaned) <= 2:
        # Check if broad phrase covers entire input
        if any(ph in joined.lower() for ph in EXPLORATORY_PHRASES) or joined.lower().strip() in {"analyze this data", "explore this dataset"}:
            return [AnalyticalIntent(raw=joined, goal="overview", is_exploratory=True, group_lines=cleaned)]

    # Otherwise, group by intent — for now, each line is an intent to preserve diversity
    # Deduplicate identical lines
    seen = set()
    intents: list[AnalyticalIntent] = []
    for ln in cleaned:
        low = ln.lower()
        if low in seen:
            continue
        seen.add(low)
        if _is_exploratory(ln):
            intents.append(AnalyticalIntent(raw=ln, goal="overview", is_exploratory=True, group_lines=[ln]))
            continue
        goal = _detect_goal(ln)
        ctype = _detect_chart_type(ln)
        agg = _detect_agg(ln)
        wants_split = any(kw in low for kw in ["split", "comma-separated", "explode", "each reason"])
        m = TOP_N_RE.search(ln)
        top_n = int(m.group(1)) if m else None
        intents.append(AnalyticalIntent(
            raw=ln, goal=goal, explicit_chart_type=ctype, explicit_agg=agg,
            wants_top_n=top_n, wants_split=wants_split, is_exploratory=False, group_lines=[ln]
        ))

    # If after parsing we have 0 intents but had lines, fallback to overview
    if not intents:
        return [AnalyticalIntent(raw=joined, goal="overview", is_exploratory=True, group_lines=cleaned)]
    return intents
