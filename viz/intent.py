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
    filters: list[dict] | None = None  # lightweight filter descriptors, e.g. [{"col": "year", "op": "in", "value": [2024, 2026]}]
    entities: list[str] | None = None  # column/entity names inferred from question
    confidence: float | None = None  # 0-1 confidence from reasoning
    reasoning: str | None = None  # brief analyst-style reasoning trace


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

# --- LLM intent extraction (primary) ---------------------------------------

# Pydantic models for structured LLM intent parsing
try:
    from pydantic import BaseModel, Field

    class LLMAnalyticalIntent(BaseModel):
        goal: str = Field(description="One of comparison|ranking|trend|distribution|relationship|composition|correlation|overview; for multi-intent like ranking+trend use the primary goal")
        explicit_chart_type: str | None = Field(default=None, description="Explicit chart type if user requested, e.g. bar, line, scatter, pie, etc.")
        explicit_agg: str | None = Field(default=None, description="Explicit aggregation if mentioned, e.g. sum, mean, count, count_distinct")
        wants_top_n: int | None = Field(default=None)
        wants_split: bool = Field(default=False)
        is_exploratory: bool = Field(default=False)
        filters: list[dict] | None = Field(default=None, description="Optional filter descriptors, e.g. {col: 'quarter', op: 'in', value: ['Q1','Q4']} or {col: 'date', op: 'last_n', time_range: '2 years'}")
        entities: list[str] | None = Field(default=None, description="Column/entity names mentioned")
        confidence: float | None = Field(default=None, description="0-1 confidence")
        reasoning: str | None = Field(default=None, description="Brief reasoning trace")
        all_goals: list[str] | None = Field(default=None, description="For multi-intent like ranking+trend, list all goals e.g. ['ranking','trend']")

    class LLMIntentList(BaseModel):
        intents: list[LLMAnalyticalIntent] = Field(description="One intent per distinct analytical question; for single-question input return 1 intent")

    class LLMVizSpecModel(BaseModel):
        intent: list[str] = Field(description="Analytical intents: trend, comparison, ranking, distribution, relationship, composition, correlation, overview (allow multi, e.g. ['ranking','trend'])")
        dimensions: list[str] = Field(default_factory=list, description="Dimension columns: region, quarter, sales_channel, date, month")
        metrics: list[dict] = Field(default_factory=list, description="Metrics as {expr: 'SUM(revenue)', agg: 'sum', alias?: str}")
        filters: list[dict] = Field(default_factory=list, description="Structured filters: {col, op, value, rank_by, time_range}")
        conditional_highlighting: dict | None = Field(default=None, description="{condition, field, op, style, annotate}")
        expected_chart: dict | None = Field(default=None, description="{family, chart_type, multi, small_multiples, stacked}")
        time_bucket: str = Field(default="none", description="Time aggregation: none, daily, weekly, monthly, quarterly, yearly")
        title: str | None = Field(default=None)
        confidence: float | None = Field(default=None)
        reasoning: str | None = Field(default=None)

    class LLMVizSpecList(BaseModel):
        vizspecs: list[LLMVizSpecModel] = Field(description="One VizSpec per question; up to 2 for dual metrics (And)")

    _HAS_PYDANTIC_INTENT = True
except Exception:
    _HAS_PYDANTIC_INTENT = False
    LLMAnalyticalIntent = None  # type: ignore
    LLMIntentList = None  # type: ignore
    LLMVizSpecModel = None  # type: ignore
    LLMVizSpecList = None  # type: ignore

INTENT_SYSTEM_PROMPT = (
    "You are a senior data analyst extracting AnalyticalIntent from a natural-language question about a dataset. "
    "Given the dataset schema (column names, roles, types, cardinality, samples) and the user's question, infer the analytical intent.\n\n"
    "Return one intent per distinct question (single input = single intent). Do NOT split one request into multiple intents.\n"
    "Fields:\n"
    "- goal: one of comparison|ranking|trend|distribution|relationship|composition|correlation|overview\n"
    "  * trend: asks about evolution over time\n"
    "  * relationship/correlation: relationship between two numerics\n"
    "  * distribution: spread / histogram / box\n"
    "  * composition: share / proportion / part-to-whole\n"
    "  * ranking: top/bottom / sorted\n"
    "  * comparison: compare across categories (default)\n"
    "- explicit_chart_type: only if user explicitly says bar/line/pie/scatter etc. Otherwise null.\n"
    "- explicit_agg: only if user explicitly says sum/average/count etc. Otherwise null.\n"
    "- wants_top_n, wants_split, is_exploratory (true for broad 'analyze this data' type).\n"
    "- entities: column/entity names referenced.\n"
    "- reasoning: 1-2 sentence trace of why this goal fits the question and data.\n"
    "Be concise and grounded in actual column names. Never invent column names."
)

VIZSPEC_SYSTEM_PROMPT = (
    "You are a senior data analyst converting a natural-language question into a Visualization Specification (VizSpec) with 7 keys: "
    "Intent, Dimensions, Metrics, Filter, Conditional highlighting, Expected chart, TimeBucket.\n\n"
    "Given dataset schema (column names, roles, types, cardinality, samples, temporal coverage) and one question, extract:\n"
    "- intent: list of analytical goals (e.g. ['ranking','trend'] for top 5 regions + trend)\n"
    "- dimensions: list of dimension columns (e.g. ['region','quarter'])\n"
    "- metrics: list of {expr, agg} e.g. [{expr:'SUM(revenue)', agg:'sum'}, {expr:'retention_rate', agg:'mean'}] — up to 2 metrics per request (reject if >2)\n"
    "- filters: list of {col, op, value, rank_by, time_range} e.g. {col:'region', op:'in_top_n', value:5, rank_by:'SUM(revenue)'} or {col:'date', op:'last_n', time_range:'2 years'} or {col:'quarter', op:'in', value:['Q1','Q4']}\n"
    "- conditional_highlighting: {condition, field, op, style, annotate} e.g. {condition:'declining retention', field:'retention_rate', op:'slope<0', style:'red', annotate:true} or null\n"
    "- expected_chart: {family, chart_type, multi, small_multiples, stacked} e.g. {family:'line', multi:true, small_multiples:true}\n"
    "- time_bucket: none|daily|weekly|monthly|quarterly|yearly (e.g. monthly for 'monthly revenue over last 2 years')\n"
    "Return one VizSpec per question; for 'And' dual metrics (e.g. '% revenue + deal count by channel') you may return up to 2 VizSpecs splitting the And (one per metric). Reject if >2 metrics/charts.\n"
    "Be grounded in actual column names. Never invent columns. Up to 2 charts per request.\n"
)

def _build_intent_user_prompt(lines: list[str], sheet_profiles: list[SheetProfile] | None) -> str:
    blocks: list[str] = []
    if sheet_profiles:
        for p in sheet_profiles:
            cols = []
            for c in p.columns:
                cols.append(f"- {c.name} (dtype {c.dtype}, role inferred, {c.unique_count} unique, samples: {', '.join(c.sample_values[:3]) or 'n/a'})")
            blocks.append(f"Sheet '{p.sheet_name}': {p.row_count} rows\n" + "\n".join(cols))
        schema_txt = "\n\n".join(blocks)
    else:
        schema_txt = "(no schema provided)"
    joined = "\n".join(f"{i+1}. {ln}" for i, ln in enumerate(lines))
    return (
        f"Dataset schema:\n{schema_txt}\n\n"
        f"User question(s) (one request = one intent, do not split):\n{joined}\n\n"
        "Return JSON {\"intents\": [...] } with one intent per question."
    )


def _build_vizspec_user_prompt(lines: list[str], sheet_profiles: list[SheetProfile] | None, data_profiles: dict | None = None) -> str:
    # Live profiler grounding: enrich schema with role/cardinality/temporal info if profiles provided
    # Per user fix: pass dataset column schema before query generation, verbatim copy instruction
    # data_profiles is dict[str, DataProfile] from viz.profiler.profile_workbook if available (live)
    blocks: list[str] = []
    if sheet_profiles:
        for p in sheet_profiles:
            cols = []
            # Try to get live DataProfile for this sheet if available
            dprof = None
            if data_profiles and p.sheet_name in data_profiles:
                dprof = data_profiles[p.sheet_name]
            elif data_profiles and len(data_profiles) == 1:
                # Fallback single sheet
                dprof = list(data_profiles.values())[0]
            for c in p.columns:
                # Live role + cardinality + top values so the LLM can rank
                # CUSTOMER (categorical entity) above DATE (temporal) for
                # ranking questions and vice versa for trends.
                role = "inferred categorical"
                extra = ""
                if dprof:
                    meta = dprof.by_name(c.name)
                    if meta:
                        role = meta.role
                        extra = f", cardinality {meta.cardinality}"
                        if meta.top_values:
                            tops = ", ".join(str(t.get("value")) for t in meta.top_values[:3])
                            extra += f", top: {tops}"
                cols.append(f"- EXACT \"{c.name}\" (role {role}, dtype {c.dtype}, {c.unique_count} unique{extra}, samples: {', '.join(c.sample_values[:3]) or 'n/a'})")
            blocks.append(f"Sheet '{p.sheet_name}': {p.row_count} rows\n" + "\n".join(cols))
        # Alias block: only aliases whose target column actually exists, so
        # the LLM maps revenue/sales/amount -> GROSS AMT instead of
        # hallucinating a column named "revenue".
        alias_txt = ""
        try:
            from viz.resolve import alias_block as _alias_block

            _all = [c.name for p in sheet_profiles for c in p.columns]
            _roles = {}
            if data_profiles:
                for _dp in data_profiles.values():
                    for _m in _dp.columns:
                        _roles[_m.name] = _m.role
            alias_txt = _alias_block(_all, _roles)
        except Exception:
            alias_txt = ""
        schema_txt = "\n\n".join(blocks)
        if alias_txt:
            schema_txt += "\n\nKnown aliases (map the user word on the left to the EXACT column on the right):\n" + alias_txt
        schema_instruction = (
            "Dataset column schema (COPY EXACT column names verbatim from list below — case, spaces, underscores must match exactly. Never paraphrase or invent columns. "
            "Use the role field: ranking/top-N questions need a categorical dimension (e.g. CUSTOMER), trend questions need the temporal column. "
            "Use the Known aliases block for user words like revenue/sales/amount. "
            "If no exact match, leave the field empty — do not guess.):\n"
            f"{schema_txt}"
        )
    else:
        schema_instruction = "Dataset schema: (no schema provided — cannot ground VizSpec, leave dimensions/metrics empty)"
    joined = "\n".join(f"{i+1}. {ln}" for i, ln in enumerate(lines))
    return (
        f"{schema_instruction}\n\n"
        f"User question(s) (one request = one VizSpec, dual And → up to 2 VizSpecs):\n{joined}\n\n"
        "Return JSON {\"vizspecs\": [...] } with VizSpec 7 keys: intent, dimensions, metrics, filters, conditional_highlighting, expected_chart, time_bucket.\n"
        "CRITICAL: For dimensions, metrics[].expr, filters[].col, filters[].rank_by, conditional_highlighting.field — COPY exact column names verbatim from Dataset column schema list above. Do not use user language like 'revenue' or 'percentage revenue distribution' — use the actual column name from the schema/aliases (e.g. 'GROSS AMT'). If the user asks for a column that does not exist (e.g. 'age'), leave that field empty."
    )


def _deterministic_vizspec_split(lines: list[str], sheet_profiles: list[SheetProfile] | None, data_profiles: dict | None = None) -> list[dict] | None:
    """Lightweight fallback for VizSpec dual And without LLM: split '… And …' into up to 2 VizSpecs.
    LLM-first with rich context (per user Q3): this is fallback only when LLM unavailable. Uses live DataProfile grounding.
    Strict transparency: user language like 'percentage revenue distribution' must not leak as column name — use actual column.
    When data_profiles provided (live), uses DataProfile.numeric_cols/categorical_cols for grounding (per user Q2 approve: Live DataProfile).
    """
    if not lines or len(lines) != 1:
        return None
    raw = lines[0]
    low = raw.lower()
    # Detect And joining two distinct analytical clauses (e.g. "% revenue … And how many deals …")
    if " and " not in low:
        return None
    # Split on And (preserve case for metrics detection)
    parts = re.split(r"\s+And\s+|\s+and\s+|\s+AND\s+", raw)
    # Only handle 2 parts with each containing a metric-like term
    if len(parts) != 2:
        return None
    p1, p2 = parts[0].strip(), parts[1].strip()
    if not p1 or not p2:
        return None
    # Heuristic: each part should contain a metric keyword or question word to be a distinct viz
    # Keep lightweight: if second part starts with how/what/show/compare etc., treat as dual
    second_is_question = any(kw in p2.lower() for kw in ["how many", "what", "show", "compare", "count", "percentage", "percent", "revenue", "deals", "channel", "sales"])
    if not second_is_question:
        return None
    # Build 2 VizSpecs with up to 1 metric each
    def _detect_agg_for_part(part: str, num_cols: list | None = None) -> str | None:
        lowp = part.lower()
        if any(kw in lowp for kw in ["percentage", "percent", "share", "proportion"]):
            return "percent"
        if "how many" in lowp:
            # "how many units" -> SUM(units); "how many customers" -> COUNT.
            # Resolve the counted noun: numeric measure => sum, else count.
            m = re.search(r"how\s+many\s+([a-z0-9][a-z0-9 _\-]*)", lowp)
            if m and num_cols:
                try:
                    from viz.resolve import score_query as _sq

                    noun = m.group(1).strip()
                    if max((_sq(noun, c) for c in num_cols), default=0) >= 1.0:
                        return "sum"
                except Exception:
                    pass
            return "count"
        if "count" in lowp or "deals" in lowp or "units" in lowp:
            return "count" if "count" in lowp else "sum"
        if "average" in lowp or "mean" in lowp or "avg" in lowp:
            return "mean"
        if "total" in lowp or "sum" in lowp:
            return "sum"
        return None

    def _detect_time_bucket(part: str) -> str:
        lowp = part.lower()
        if "month over month" in lowp or "by month" in lowp or "monthly" in lowp:
            return "monthly"
        if "quarter" in lowp:
            return "quarterly"
        if "yearly" in lowp or "by year" in lowp or "year over year" in lowp:
            return "yearly"
        if "weekly" in lowp:
            return "weekly"
        return "none"

    def _norm_col(col: str) -> str:
        import re as _re2
        return _re2.sub(r"[^a-z0-9]+", " ", col.lower()).strip()

    # Use live DataProfile if available for grounding (per user Q2 approve)
    # Build live DataProfile maps if data_profiles provided
    live_numeric: set[str] = set()
    live_categorical: set[str] = set()
    live_temporal: set[str] = set()
    if data_profiles:
        for dp in data_profiles.values():
            live_numeric.update(dp.numeric_cols)
            live_categorical.update(dp.categorical_cols)
            live_temporal.update(dp.temporal_cols)
    # Use sheet_profiles to ground dimensions: pick categorical for dimensions, numeric for metrics
    vizspecs: list[dict] = []
    for idx, part in enumerate([p1, p2]):
        dims: list[str] = []
        if sheet_profiles:
            all_cols = [c.name for p in sheet_profiles for c in p.columns]
            part_norm = _norm_col(part)
            part_tokens = set(part_norm.split())
            # Prefer categorical cols for dimensions (live DataProfile if available)
            cat_cols = []
            if live_categorical:
                # Preserve source/profile order. Sets make dimension selection
                # nondeterministic and can turn an explicit region grouping
                # into an unrelated low-cardinality column.
                cat_cols = [
                    c.name
                    for p in sheet_profiles
                    for c in p.columns
                    if c.name in live_categorical
                ]
            else:
                for p in sheet_profiles:
                    for c in p.columns:
                        if c.dtype.lower() in ("object", "string") or "object" in c.dtype.lower():
                            cat_cols.append(c.name)
            # Try categorical that appears (token overlap or full phrase)
            for col in cat_cols:
                cnorm = _norm_col(col)
                ctoks = set(cnorm.split())
                if cnorm in part_norm or (ctoks & part_tokens):
                    # At least one token of col appears in part
                    if any(tok in part_tokens for tok in ctoks):
                        dims.append(col)
                        break
            if not dims:
                # Fallback any col that appears (normalized token overlap)
                for col in all_cols:
                    cnorm = _norm_col(col)
                    ctoks = set(cnorm.split())
                    if cnorm and (cnorm in part_norm or (ctoks & part_tokens)):
                        # Only allow categorical as dimension
                        if col in cat_cols:
                            dims.append(col)
                            break
            if not dims and cat_cols:
                # Fallback first categorical (e.g., sales_channel) for And second part
                dims.append(cat_cols[0])
        # Metrics: prefer numeric columns (live DataProfile if available)
        metrics = []
        if sheet_profiles:
            def _is_float_local2(s: str) -> bool:
                try:
                    float(str(s).replace("$","").replace(",","").strip())
                    return True
                except Exception:
                    return False
            num_cols = []
            agg = _detect_agg_for_part(part, list(live_numeric) if live_numeric else None)
            if live_numeric:
                num_cols = [
                    c.name
                    for p in sheet_profiles
                    for c in p.columns
                    if c.name in live_numeric
                ]
            else:
                for p in sheet_profiles:
                    for c in p.columns:
                        is_num_dtype = c.dtype.lower() in ("int64","int32","float64","float32","int16","float16","float","int")
                        is_num_sample = False
                        if not is_num_dtype and c.sample_values:
                            try:
                                cleaned = [str(v).replace("$","").replace(",","").strip() for v in c.sample_values[:3] if v]
                                if cleaned and all(_is_float_local2(s) for s in cleaned if s):
                                    is_num_sample = True
                            except Exception:
                                pass
                        if is_num_dtype or is_num_sample:
                            num_cols.append(c.name)
                # Aggregation needs the numeric inventory: "how many units"
                # resolves the noun (units -> PCS) to decide sum vs count.
                agg = _detect_agg_for_part(part, num_cols)
                part_norm2 = _norm_col(part)
            part_tokens2 = set(part_norm2.split())
            for col in num_cols:
                cnorm = _norm_col(col)
                ctoks = set(cnorm.split())
                if cnorm and (cnorm in part_norm2 or (ctoks & part_tokens2)):
                    metrics = [{"expr": col, "agg": agg or "sum"}]
                    break
            if not metrics:
                # Fallback any numeric col if none matched but metric needed
                if num_cols:
                    # For percent case, prefer revenue-like
                    for col in num_cols:
                        if "revenue" in col.lower() and ("percent" in part.lower() or "revenue" in part.lower()):
                            metrics = [{"expr": col, "agg": agg or "sum"}]
                            break
                    if not metrics:
                        for col in num_cols:
                            if "gross" in col.lower() and ("amount" in part.lower() or "amt" in part.lower()):
                                metrics = [{"expr": col, "agg": agg or "sum"}]
                                break
                    if not metrics:
                        # Generic fallback: amount/actual amount -> GROSS AMT
                        for col in num_cols:
                            if "gross" in col.lower() and "amount" in part.lower():
                                metrics = [{"expr": col, "agg": agg or "sum"}]
                                break
                    if not metrics and "amount" in part.lower():
                        # Fallback to GROSS AMT if exists
                        for col in num_cols:
                            if "gross" in col.lower():
                                metrics = [{"expr": col, "agg": agg or "sum"}]
                                break
                    if not metrics:
                        pass
        if not metrics:
            # Resolve THIS part's metric via the single resolver ("units" ->
            # PCS), never by guessing GROSS AMT / num_cols[0]: a wrong guess
            # here leaks one half of an And-question into the other half.
            # Unresolvable -> leave empty so validation skips with
            # Did-you-mean instead of wrong data.
            try:
                from viz.resolve import score_query as _sq

                _best, _bs = None, 0.0
                for col in num_cols:
                    _s = _sq(part, col)
                    if _s > _bs:
                        _best, _bs = col, _s
                if _best and _bs >= 1.0:
                    metrics = [{"expr": _best, "agg": agg or "sum"}]
                else:
                    metrics = []
            except Exception:
                metrics = []
        # Anaphora: if second part has "each" and first part has Customer/Style dimension, inherit
        goal = _detect_goal(part)
        # Determine expected chart family more accurately
        exp_family = "composition" if "percent" in part.lower() or "share" in part.lower() else "comparison"
        if "top" in part.lower():
            exp_family = "ranking"
        explicit_type = _detect_chart_type(part)
        vizspecs.append({
            "intent": [goal],
            "dimensions": dims[:1],
            "metrics": metrics,
            "filters": [],
            "conditional_highlighting": None,
            "expected_chart": {"family": exp_family, "chart_type": explicit_type},
            "time_bucket": "none",
            "title": part[:90],
            "confidence": 0.6,
            "reasoning": "Deterministic And split",
        })
    # Anaphora resolution: if second part is "how many units did each buy?" inherit Customer from first
    if len(vizspecs) == 2:
        p2_low = p2.lower()
        if "each" in p2_low and vizspecs[1]["dimensions"] == [vizspecs[1]["dimensions"][0]] if vizspecs[1]["dimensions"] else False:
            # If second dims is fallback Style but first is Customer, and second contains "each", inherit
            if vizspecs[1]["dimensions"] and vizspecs[0]["dimensions"] and vizspecs[1]["dimensions"][0] != vizspecs[0]["dimensions"][0]:
                # Check if second part has no explicit customer token but first does
                if "customer" not in p2_low and "customer" in p1.lower():
                    vizspecs[1]["dimensions"] = vizspecs[0]["dimensions"][:1]
                # Also handle top N inheritance for Q2
                # If first part has top 10 and second part is "how many units", it should also be top 10
        # Handle top N filter for And split: if raw contains top N, apply to both vizspecs where relevant
        m_top_all = re.search(r"top\s+(\d+)", low)
        if m_top_all:
            n_top_all = int(m_top_all.group(1))
            # Determine rank_by from first part's metric (e.g., revenue for top 10 customers by revenue) to share across both
            first_rank_col = None
            if vizspecs and vizspecs[0]["metrics"]:
                first_rank_col = vizspecs[0]["metrics"][0].get("expr", "")
                # Clean to column name
                import re as _re2
                first_rank_col = _re2.sub(r".*\((.*)\).*", r"\1", first_rank_col) if "(" in first_rank_col else first_rank_col
                first_rank_col = first_rank_col.strip().strip("\"'`")
                # Verify it exists in all_cols
                if first_rank_col not in [c.name for p in sheet_profiles for c in p.columns]:
                    first_rank_col = None
            if not first_rank_col:
                # Fallback: find numeric that appears near top in raw
                for col in [c.name for p in sheet_profiles for c in p.columns]:
                    if col.lower() in low:
                        for p2 in sheet_profiles:
                            for c2 in p2.columns:
                                if c2.name == col and c2.dtype.lower() in ("int64","int32","float64","float32","int16","float16"):
                                    first_rank_col = col
                                    break
                            if first_rank_col:
                                break
                    if first_rank_col:
                        break
            for v in vizspecs:
                if not v["filters"]:
                    rank_col = first_rank_col
                    # Fallback to revenue or first metric's expr if not found
                    if not rank_col:
                        for m in v["metrics"]:
                            if "revenue" in m.get("expr","").lower():
                                rank_col = m["expr"]
                                break
                    if not rank_col and v["metrics"]:
                        rank_col = v["metrics"][0].get("expr", "")
                    if rank_col and v["dimensions"]:
                        v["filters"] = [{"col": v["dimensions"][0], "op": "in_top_n", "value": n_top_all, "rank_by": f"SUM({rank_col})"}]
    # Enforce up to 2 metrics total
    total = sum(len(v["metrics"]) for v in vizspecs)
    if total > 2:
        return None
    return vizspecs


def _llm_vizspecs(lines: list[str], sheet_profiles: list[SheetProfile] | None, data_profiles: dict | None = None) -> list[dict] | None:
    """LLM VizSpec parsing (advisory): 7 keys with live profiler grounding.

    Column names from the LLM are NEVER trusted downstream: orchestrate's
    _validate_vizspec re-resolves every dimension/metric/filter through the
    single resolver (soft-correct obvious aliases, hard skip with Did-you-mean
    otherwise). Returns None when no key is configured or the call fails.
    """
    if not _HAS_PYDANTIC_INTENT:
        return None
    try:
        from llm import llm_structured, available_provider
        if available_provider() is not None:
            user = _build_vizspec_user_prompt(lines, sheet_profiles, data_profiles)
            out = llm_structured(VIZSPEC_SYSTEM_PROMPT, user, LLMVizSpecList)  # type: ignore
            # Process LLM VizSpecs below (reuse logic after)
            vizspecs_llm: list[dict] = []
            for vs in out.vizspecs:
                if len(vs.metrics) > 2:
                    continue
                vizspecs_llm.append({
                    "intent": vs.intent,
                    "dimensions": vs.dimensions,
                    "metrics": [m if isinstance(m, dict) else m.model_dump() for m in vs.metrics],
                    "filters": vs.filters,
                    "conditional_highlighting": vs.conditional_highlighting,
                    "expected_chart": vs.expected_chart,
                    "time_bucket": vs.time_bucket,
                    "title": vs.title,
                    "confidence": vs.confidence,
                    "reasoning": vs.reasoning,
                })
            if len(vizspecs_llm) > 2:
                vizspecs_llm = vizspecs_llm[:2]
            total_metrics = sum(len(v.get("metrics", [])) for v in vizspecs_llm)
            if total_metrics > 2:
                kept: list[dict] = []
                cnt = 0
                for v in vizspecs_llm:
                    mlen = len(v.get("metrics", []))
                    if cnt + mlen <= 2:
                        kept.append(v)
                        cnt += mlen
                    elif cnt < 2:
                        v2 = dict(v)
                        v2["metrics"] = v2["metrics"][: 2 - cnt]
                        kept.append(v2)
                        cnt = 2
                        break
                vizspecs_llm = kept
            if vizspecs_llm:
                return vizspecs_llm
    except Exception:
        pass
    return None


def parse_vizspec(lines: list[str], sheet_profiles: list[SheetProfile] | None, data_profiles: dict | None = None) -> list[dict] | None:
    """Deterministic-first VizSpec parsing (Option A contract).

    Deterministic And-split / time / top-N construction runs FIRST: precise,
    free, and reproducible. The LLM (_llm_vizspecs) is advisory-only and runs
    solely when deterministic yields nothing usable. Column names from either
    path are never trusted: orchestrate re-resolves every dimension/metric/
    filter through the single resolver (soft-correct aliases, hard skip with
    Did-you-mean otherwise). Returns None when neither path produces VizSpecs
    (intent-layer fallback).
    """
    if not _HAS_PYDANTIC_INTENT:
        return None
    try:
        det = _deterministic_vizspecs(lines, sheet_profiles, data_profiles)
    except Exception:
        det = None
    if det:
        return det
    return _llm_vizspecs(lines, sheet_profiles, data_profiles)


def _deterministic_vizspecs(lines: list[str], sheet_profiles: list[SheetProfile] | None, data_profiles: dict | None = None) -> list[dict] | None:
    """Deterministic VizSpec construction (no LLM): And-split + time/top-N single."""
    _ = data_profiles  # reserved for future live-grounded refinements
    # Deterministic And split (soft auto-correct synonyms, hard skip on hallucinations)
    det = _deterministic_vizspec_split(lines, sheet_profiles)
    if det:
        return det
    # Fallback for single question with time_bucket / top-N without And (e.g., "total sales by month", "top 5 styles trend")
    if len(lines) == 1 and sheet_profiles:
        raw = lines[0]
        low = raw.lower()
        has_time = any(kw in low for kw in ["by month", "monthly", "month over month", "by quarter", "quarterly", "by year", "yearly", "over time", "trend"])
        has_top = bool(re.search(r"top\s+\d+", low))
        if has_time or has_top:
            # Try to build single VizSpec deterministically with live DataProfile context
            try:
                # Detect time_bucket
                tb = "none"
                if "month" in low:
                    tb = "monthly"
                elif "quarter" in low:
                    tb = "quarterly"
                elif "year" in low:
                    tb = "yearly"
                # Detect dimensions: look for Style, region, etc., and temporal
                all_cols = [c.name for p in sheet_profiles for c in p.columns]
                # Find categorical for Style/region
                dims: list[str] = []
                for p in sheet_profiles:
                    for c in p.columns:
                        col = c.name
                        # A metric mentioned in the question must not also
                        # become a grouping dimension just because its name
                        # shares a token with the request.
                        dtype = c.dtype.lower()
                        is_temporal = any(token in col.lower() for token in ("date", "time", "year", "month"))
                        is_categorical = dtype in ("object", "string", "category") or "object" in dtype
                        if not (is_temporal or is_categorical):
                            continue
                        cnorm = _norm(col)
                        if any(tok in low for tok in cnorm.split()):
                            if col not in dims:
                                dims.append(col)
                # If no dims but has_time, try to find Style-like categorical
                if not dims:
                    for col in all_cols:
                        if "style" in col.lower() and "style" in low:
                            dims.append(col)
                            break
                def _is_float_local(s: str) -> bool:
                    try:
                        float(s)
                        return True
                    except Exception:
                        return False
                # Metrics: find numeric that appears (include GROSS AMT via sample numeric check)
                num_cols = []
                for p in sheet_profiles:
                    for c in p.columns:
                        is_num_dtype = c.dtype.lower() in ("int64","int32","float64","float32","int16","float16","float","int")
                        # Also consider object columns where samples look numeric (e.g., GROSS AMT "$1,200")
                        is_num_sample = False
                        if not is_num_dtype and c.sample_values:
                            try:
                                cleaned = [str(v).replace("$","").replace(",","").strip() for v in c.sample_values[:3] if v]
                                if cleaned and all(_is_float_local(s) for s in cleaned if s):
                                    is_num_sample = True
                            except Exception:
                                pass
                        if is_num_dtype or is_num_sample:
                            num_cols.append(c.name)
                metrics = []
                # Distinct counts target entity columns, which are usually
                # categorical and therefore absent from num_cols.
                if any(term in low for term in ("count unique", "count distinct", "unique count", "distinct count")):
                    distinct_match = re.search(
                        r"(?:count\s+(?:unique|distinct)|(?:unique|distinct)\s+count)\s+(?:of\s+)?([a-z0-9][a-z0-9 _-]*?)(?:\s+by\b|\s+per\b|\?|$)",
                        raw,
                        re.IGNORECASE,
                    )
                    query = distinct_match.group(1).strip() if distinct_match else raw
                    try:
                        from viz.resolve import score_query as _distinct_score

                        best_col, best_score = None, 0.0
                        for col in all_cols:
                            score = _distinct_score(query, col)
                            if score > best_score:
                                best_col, best_score = col, score
                        if best_col and best_score >= 1.0:
                            metrics = [{"expr": best_col, "agg": "count_distinct"}]
                    except Exception:
                        pass
                for col in num_cols:
                    cnorm = _norm(col)
                    if not metrics and (any(tok in low for tok in cnorm.split()) or "sales" in low and "gross" in col.lower() or "amount" in low and "amt" in col.lower()):
                        metrics.append({"expr": col, "agg": "sum"})
                        break
                if not metrics and num_cols:
                    # Fallback to GROSS AMT for sales amount
                    for col in num_cols:
                        if "gross" in col.lower() and "amt" in col.lower() and ("sales" in low or "amount" in low):
                            metrics = [{"expr": col, "agg": "sum"}]
                            break
                    if not metrics:
                        # Alias-aware fallback via per-word resolver scoring
                        # ("revenue" -> GROSS AMT beats "units" -> PCS here
                        # because the question names revenue). Never guess
                        # num_cols[0]: leave empty so validation emits a
                        # skipped spec with Did-you-mean.
                        try:
                            from viz.resolve import score_query as _sq

                            _best, _sc = None, 0.0
                            for col in num_cols:
                                _s = _sq(raw, col)
                                if _s > _sc:
                                    _best, _sc = col, _s
                            if _best and _sc >= 1.0:
                                metrics = [{"expr": _best, "agg": "sum"}]
                        except Exception:
                            pass
                # Filters: top N
                filters = []
                m_top = re.search(r"top\s+(\d+)", low)
                if m_top:
                    n_top = int(m_top.group(1))
                    # Find rank_by col via single resolver (revenue ->
                    # GROSS AMT). No num_cols[0] guess: without a resolved
                    # metric the Top-N filter is omitted and ranking falls
                    # back to the chart metric / count.
                    rank_col = None
                    for col in num_cols:
                        if col.lower() in low:
                            rank_col = col
                            break
                    if not rank_col and num_cols:
                        try:
                            from viz.resolve import score_query as _sq2

                            _rb, _sc2 = None, 0.0
                            for col in num_cols:
                                _s = _sq2(raw, col)
                                if _s > _sc2:
                                    _rb, _sc2 = col, _s
                            rank_col = _rb if _rb and _sc2 >= 1.0 else None
                        except Exception:
                            rank_col = None
                    # Use first dims as rank col if Style
                    rank_dim = dims[0] if dims else (all_cols[0] if all_cols else "")
                    if rank_dim and rank_col:
                        filters.append({"col": rank_dim, "op": "in_top_n", "value": n_top, "rank_by": f"SUM({rank_col})"})
                # Time bucket already tb
                # Intent
                intent_list = []
                if has_top:
                    intent_list.append("ranking")
                if has_time or "trend" in low or "change" in low:
                    intent_list.append("trend")
                if not intent_list:
                    intent_list = ["comparison"]
                # Conditional highlighting for declining
                cond = None
                if "declining" in low:
                    cond = {"condition": "declining retention", "field": "retention_rate", "op": "slope<0", "style": "red", "annotate": True}
                # Build single VizSpec
                dims_for_spec = dims[:2] if dims else []
                # Ensure temporal dim for time_bucket
                if tb != "none" and not any(d.lower() in ("month","quarter","year") for d in dims_for_spec):
                    # Find temporal col
                    for p in sheet_profiles:
                        for c in p.columns:
                            if "date" in c.name.lower() or "time" in c.name.lower():
                                dims_for_spec.append(c.name)
                                break
                        if dims_for_spec and tb != "none":
                            break
                # Deduplicate
                dims_for_spec = list(dict.fromkeys(dims_for_spec))[:2]
                if tb != "none":
                    temporal_names = {
                        c.name
                        for p in sheet_profiles
                        for c in p.columns
                        if any(token in c.name.lower() for token in ("date", "time", "year", "month"))
                    }
                    dims_for_spec.sort(key=lambda name: 0 if name in temporal_names else 1)
                viz = {
                    "intent": intent_list,
                    "dimensions": dims_for_spec,
                    "metrics": metrics[:2],
                    "filters": filters,
                    "conditional_highlighting": cond,
                    "expected_chart": {
                        "family": "line" if "trend" in intent_list else "bar",
                        "chart_type": _detect_chart_type(raw),
                        "multi": "trend" in intent_list,
                    },
                    "time_bucket": tb,
                    "title": raw[:90],
                    "confidence": 0.6,
                    "reasoning": "Deterministic single VizSpec fallback for time/top-N",
                }
                # Validate not empty
                if viz["dimensions"] or viz["metrics"]:
                    return [viz]
            except Exception as e:
                print(f"deterministic single vizspec failed: {e}")
                import traceback; traceback.print_exc()
                pass
    # No deterministic construction -> caller tries the advisory LLM path
    return None

def _llm_parse_intents(lines: list[str], sheet_profiles: list[SheetProfile] | None) -> list[AnalyticalIntent] | None:
    """Try LLM reasoning for intent. Returns None if unavailable/failed to trigger fallback."""
    if not _HAS_PYDANTIC_INTENT:
        return None
    try:
        from llm import llm_structured, available_provider, LLMError
    except Exception:
        return None
    if available_provider() is None:
        return None
    try:
        user = _build_intent_user_prompt(lines, sheet_profiles)
        # LLM call is the reasoning step: understand NL and extract intent grounded in schema
        out = llm_structured(INTENT_SYSTEM_PROMPT, user, LLMIntentList)  # type: ignore
    except Exception:
        return None
    intents: list[AnalyticalIntent] = []
    for idx, it in enumerate(out.intents):
        # Normalize goal
        goal = (it.goal or "comparison").strip().lower()
        valid_goals = {"comparison","ranking","trend","distribution","relationship","composition","correlation","overview"}
        if goal not in valid_goals:
            goal = "comparison"
        # Use raw line as single source; do not split - one intent per input
        raw = lines[idx] if idx < len(lines) else lines[0] if lines else ""
        intents.append(AnalyticalIntent(
            raw=raw,
            goal=goal,
            explicit_chart_type=it.explicit_chart_type,
            explicit_agg=it.explicit_agg,
            wants_top_n=it.wants_top_n,
            wants_split=bool(it.wants_split),
            is_exploratory=bool(it.is_exploratory),
            group_lines=[raw],
            filters=it.filters,
            entities=it.entities,
            confidence=it.confidence,
            reasoning=it.reasoning,
        ))
    # Enforce one-intent-per-request: if caller sent one line, return exactly 1
    if len(lines) == 1 and len(intents) > 1:
        intents = intents[:1]
        intents[0].raw = lines[0]
        intents[0].group_lines = [lines[0]]
    return intents if intents else None


def _deterministic_parse_intents(lines: list[str], sheet_profiles: list[SheetProfile] | None = None) -> list[AnalyticalIntent]:
    """Keyword-lightweight fallback when LLM unavailable: still infers goal but as secondary signal."""
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    if not cleaned:
        return [AnalyticalIntent(raw="", goal="overview", is_exploratory=True, group_lines=[])]

    joined = " ".join(cleaned)
    if _is_exploratory(joined) and len(cleaned) <= 2:
        if any(ph in joined.lower() for ph in EXPLORATORY_PHRASES) or joined.lower().strip() in {"analyze this data", "explore this dataset"}:
            return [AnalyticalIntent(raw=joined, goal="overview", is_exploratory=True, group_lines=cleaned, confidence=0.6, reasoning="Fallback: broad exploratory phrase")]

    seen: set[str] = set()
    intents: list[AnalyticalIntent] = []
    for ln in cleaned:
        low = ln.lower()
        if low in seen:
            continue
        seen.add(low)
        if _is_exploratory(ln):
            intents.append(AnalyticalIntent(raw=ln, goal="overview", is_exploratory=True, group_lines=[ln], confidence=0.6, reasoning="Fallback: exploratory"))
            continue
        goal = _detect_goal(ln)
        ctype = _detect_chart_type(ln)
        agg = _detect_agg(ln)
        wants_split = any(kw in low for kw in ["split", "comma-separated", "explode", "each reason"])
        m = TOP_N_RE.search(ln)
        top_n = int(m.group(1)) if m else None
        intents.append(AnalyticalIntent(
            raw=ln, goal=goal, explicit_chart_type=ctype, explicit_agg=agg,
            wants_top_n=top_n, wants_split=wants_split, is_exploratory=False, group_lines=[ln],
            confidence=0.5, reasoning="Fallback: keyword-lightweight goal detection"
        ))

    if not intents:
        return [AnalyticalIntent(raw=joined, goal="overview", is_exploratory=True, group_lines=cleaned, confidence=0.5, reasoning="Fallback: empty")]
    return intents


def _needs_llm_assist(intent: AnalyticalIntent) -> bool:
    """True when deterministic parsing produced only a bare default.

    A bare default (goal=comparison with no explicit chart/agg, no top-N,
    no split, non-exploratory) means the keywords found nothing to hold on
    to — exactly where an LLM goal read adds value (e.g. "how has revenue
    changed" -> trend). Anything informative stays purely deterministic.
    """
    if intent.is_exploratory:
        return False
    return (
        intent.goal == "comparison"
        and intent.explicit_chart_type is None
        and intent.explicit_agg is None
        and intent.wants_top_n is None
        and not intent.wants_split
    )


def _graft_llm_goal(det: AnalyticalIntent, llm_one: AnalyticalIntent) -> AnalyticalIntent:
    """Adopt the LLM's goal-level read; keep deterministic grounding.

    raw/group_lines/filters/entities stay deterministic — the LLM contributes
    goal, explicit chart/agg, top-N and split flags only. Column names from
    the LLM are never adopted (single-resolver contract).
    """
    valid_goals = {"comparison", "ranking", "trend", "distribution",
                   "relationship", "composition", "correlation", "overview"}
    goal = (llm_one.goal or "comparison").strip().lower()
    det.goal = goal if goal in valid_goals else det.goal
    if llm_one.explicit_chart_type:
        det.explicit_chart_type = llm_one.explicit_chart_type
    if llm_one.explicit_agg:
        det.explicit_agg = llm_one.explicit_agg
    if llm_one.wants_top_n:
        det.wants_top_n = llm_one.wants_top_n
    if llm_one.wants_split:
        det.wants_split = True
    det.confidence = llm_one.confidence or 0.7
    det.reasoning = (llm_one.reasoning or "LLM goal assist on deterministic intent")
    return det


def parse_intents(lines: list[str], sheet_profiles: list[SheetProfile] | None = None) -> list[AnalyticalIntent]:
    """Public entry: deterministic-first intent, LLM advisory only.
    One request = one intent: do not split single input into multiple intents.

    Option A contract: deterministic parsing always runs first. The LLM is
    consulted solely to refine the *goal* of bare-default intents and never
    supplies column names (candidates/validation re-resolve everything
    through the single resolver).
    """
    cleaned = [ln.strip() for ln in lines if ln.strip()]
    if not cleaned:
        return [AnalyticalIntent(raw="", goal="overview", is_exploratory=True, group_lines=[], confidence=0.9, reasoning="Empty")]

    # Single-question path: one intent only (per product decision)
    if len(cleaned) == 1:
        single = cleaned[0]
        det = _deterministic_parse_intents([single], sheet_profiles)
        det_one = det[0] if det else None
        if det_one is not None and _needs_llm_assist(det_one):
            try:
                llm_res = _llm_parse_intents([single], sheet_profiles)
                if llm_res:
                    return [_graft_llm_goal(det_one, llm_res[0])]
            except Exception:
                pass
        return [det_one] if det_one is not None else _deterministic_parse_intents([single], sheet_profiles)

    # Multi-line input: deterministic per line; LLM assists bare defaults only.
    det_list = _deterministic_parse_intents(cleaned, sheet_profiles)
    bare_idx = [i for i, it in enumerate(det_list) if _needs_llm_assist(it)]
    if bare_idx:
        try:
            bare_lines = [det_list[i].raw for i in bare_idx]
            llm_batch = _llm_parse_intents(bare_lines, sheet_profiles)
            if llm_batch and len(llm_batch) == len(bare_idx):
                for i, llm_one in zip(bare_idx, llm_batch):
                    det_list[i] = _graft_llm_goal(det_list[i], llm_one)
        except Exception:
            pass
    return det_list
