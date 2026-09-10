"""ReAct-style agent loop.

For each planned ChartSpec the agent may use three tools -- inspect_data,
run_code (sandboxed), create_chart -- with an LLM in the loop writing the pandas
snippet and reacting to errors (retry with corrected code).  Without an API key
it falls back to a deterministic code generator that still runs through the same
sandboxed tools.  Skipped specs pass straight through, untouched.
"""

from __future__ import annotations

import re
from typing import Sequence

import pandas as pd
from pydantic import BaseModel

from ingestion import Workbook
from llm import LLMError, llm_structured
from planning import recommend_charts
from schemas import ChartResult, ChartSpec, SheetProfile
from tools import create_chart, inspect_data, run_code
from tools.create_chart import ChartBuildError
from tools.data_quality import (
    MONTH_LABEL_RE,
    NUMERIC_DATE_RE,
    apply_quarantine,
    blank_row_count,
    quarantine_report,
    should_quarantine,
)
from tools.semantic_validation import validate_chart

MAX_ATTEMPTS = 2

AGENT_SYSTEM_PROMPT = (
    "You are the execution engine of an agentic chart-builder. You are given one "
    "ChartSpec and the schema of its sheet. Write a single Python snippet using "
    "a pandas DataFrame already loaded as `df`. The snippet MUST end by assigning "
    "its output to a variable named `result` (a DataFrame or scalar). "
    "Rules: use only pandas (`pd`) and the `df` object; no imports, no file or "
    "network access, no eval/exec/open; do not touch dunder attributes. "
    "If the spec contains data_notes, follow those instructions for data transformation "
    "(e.g. split comma-separated values into separate rows using str.split + explode, "
    "use nunique() for count-distinct, apply top-N limits). "
    "When grouping by a non-temporal categorical dimension, exclude month-like "
    "cells (e.g. 'Feb-22', '09-14-21' — misaligned source rows) from the grouping. "
    "For a bar/pie/donut/grouped/stacked chart produce one row per category (or per x,group) with aggregated value. "
    "For a line/area chart produce one row per time point (aggregated if needed). "
    "For a scatter chart produce the raw x/y rows. "
    "For a histogram produce the raw numeric column rows (dropna). "
    "For a box plot produce raw x(group) and y rows (dropna). "
    "For a heatmap produce a correlation-ready numeric subset (df.select_dtypes). "
    "Reply with JSON {\"code\": \"...\"} containing only the snippet."
)


class CodeResult(BaseModel):
    code: str


def _translate_filter(desc: str, df: pd.DataFrame):
    m = re.match(r"^\s*([\w\s]+?)\s*(==|!=|>=|<=|>|<|=)\s*(.+?)\s*$", desc)
    if not m:
        return None
    col_name, op, val = m.groups()
    op = "==" if op == "=" else op
    for c in df.columns:
        if c.lower() == col_name.strip().lower():
            col_name = c
            break
    else:
        return None
    if col_name not in df.columns:
        return None
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", val.strip()):
        val_out = float(val.strip())
    elif val.strip().startswith('"') or val.strip().startswith("'"):
        val_out = val.strip().strip('"').strip("'")
    else:
        val_out = val.strip()
    if isinstance(val_out, str):
        return (f"df[{col_name!r}]", f"{op} {val_out!r}")
    return (f"df[{col_name!r}]", f"{op} {val_out}")


def _fg(col: str) -> str:
    """Python-compatible string literal for a column name."""
    return repr(col)


def _codegen_deterministic(spec: ChartSpec, df: pd.DataFrame) -> str:
    agg = spec.agg_function or "sum"
    # Handle percent as sum (percentage will be normalized in chart layer)
    if agg == "percent":
        agg = "sum"
    x, y = spec.x, spec.y
    cols = list(df.columns)
    notes = (spec.data_notes or "").lower()
    derived_margin = "derived metric: profit_margin" in notes
    margin_sales = margin_cost = None
    if derived_margin:
        m_sales = re.search(r"sales=([^;.]+?)\s*;", spec.data_notes or "", re.IGNORECASE)
        m_cost = re.search(r"cost=([^;.]+)", spec.data_notes or "", re.IGNORECASE)
        margin_sales = m_sales.group(1).strip() if m_sales else None
        margin_cost = m_cost.group(1).strip() if m_cost else None
        if margin_sales not in cols or margin_cost not in cols:
            derived_margin = False
    # Normalize group_by coherence at codegen level as well
    GROUP_AWARE = {"grouped_bar", "stacked_bar", "stacked_100", "line", "area", "scatter", "heatmap", "boxplot"}
    if spec.group_by and spec.group_by in (x, y):
        spec.group_by = None
    if spec.chart_type not in GROUP_AWARE and spec.group_by:
        spec.group_by = None

    if not y and cols and agg not in ("count", "count_distinct"):
        numeric = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        y = numeric[0] if numeric else None

    chunks: list[str] = []
    # Late cleaning for currency strings ($ , %): ensure y is numeric before any agg (Top 10 concatenated bug)
    if y and y in cols and agg not in ("count", "count_distinct") and not any(t in notes for t in ("nunique", "count distinct")):
        # Use deep cleaning like histogram branch, preserve raw for display but ensure numeric for sum
        chunks.append(f"df[{_fg(y)}] = pd.to_numeric(df[{_fg(y)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
    if derived_margin and margin_sales and margin_cost:
        chunks.append(f"df[{_fg(margin_sales)}] = pd.to_numeric(df[{_fg(margin_sales)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
        chunks.append(f"df[{_fg(margin_cost)}] = pd.to_numeric(df[{_fg(margin_cost)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
        # Don't dropna yet fully, keep for later dropna in groupby paths, but at least ensure numeric
    # Also clean rank_col for Top-N if present in data_notes
    if notes and "top" in notes.lower():
        import re as _re_rank
        m_rank = _re_rank.search(r"top\s+\d+\s+by\s+([^\s;,\.]+)", notes, re.IGNORECASE)
        if m_rank:
            rank_col_raw = m_rank.group(1).strip().strip("'\"")
            import re as _re3
            rank_col_clean = _re3.sub(r".*\((.*)\).*", r"\1", rank_col_raw) if "(" in rank_col_raw else rank_col_raw
            rank_col_clean = rank_col_clean.strip()
            if rank_col_clean and rank_col_clean in cols:
                chunks.append(f"df[{_fg(rank_col_clean)}] = pd.to_numeric(df[{_fg(rank_col_clean)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")

    # Misaligned-row quarantine: month-like cells ("Feb-22", "09-14-21")
    # stored inside a NON-temporal dimension never belong to that grouping.
    # Emitted before ranking/aggregation so top-N and totals are computed on
    # clean groups; the verifier applies the identical rule host-side.
    # Sandbox-safe: plain str.match with literal patterns, no imports.
    if x and x in cols and should_quarantine(df, x):
        chunks.append(
            f"_qlab = df[{_fg(x)}].astype(str); "
            f"df = df[~(_qlab.str.match({MONTH_LABEL_RE!r}, na=False).fillna(False) | "
            f"_qlab.str.match({NUMERIC_DATE_RE!r}, na=False).fillna(False))]"
        )
        if spec.data_notes and "quarantined" not in spec.data_notes.lower():
            try:
                object.__setattr__(spec, "data_notes",
                                   (spec.data_notes + " Quarantined misaligned month-like values.").strip())
            except Exception:
                spec.data_notes = (spec.data_notes + " Quarantined misaligned month-like values.").strip()

    # Time bucket: derive period column if spec asks for monthly/quarterly/yearly
    if notes and "time bucket:" in notes:
        import re as _re_tb
        m_tb = _re_tb.search(r"time bucket:\s*(monthly|quarterly|yearly|weekly|daily)", notes)
        if m_tb and x and x in cols:
            bucket = m_tb.group(1)
            # Try to parse temporal column to datetime then bucket
            if bucket == "monthly":
                chunks.append(f"df[{_fg(x)}] = pd.to_datetime(df[{_fg(x)}], errors='coerce', format='mixed')")
                chunks.append(f"df = df.dropna(subset=[{_fg(x)}])")
                chunks.append(f"df['_time_bucket'] = df[{_fg(x)}].dt.to_period('M').astype(str)")
                # Keep the planned source column on the spec. The derived
                # bucket exists only in the sandbox result and must not leak
                # into stored specs used by later executions/refinements.
                x = "_time_bucket"
            elif bucket == "quarterly":
                chunks.append(f"df[{_fg(x)}] = pd.to_datetime(df[{_fg(x)}], errors='coerce', format='mixed')")
                chunks.append(f"df = df.dropna(subset=[{_fg(x)}])")
                chunks.append(f"df['_time_bucket'] = df[{_fg(x)}].dt.to_period('Q').astype(str)")
                x = "_time_bucket"
            elif bucket == "yearly":
                chunks.append(f"df[{_fg(x)}] = pd.to_datetime(df[{_fg(x)}], errors='coerce', format='mixed')")
                chunks.append(f"df = df.dropna(subset=[{_fg(x)}])")
                chunks.append(f"df['_time_bucket'] = df[{_fg(x)}].dt.year.astype(str)")
                x = "_time_bucket"
            elif bucket in ("weekly", "daily"):
                frequency = "W-SUN" if bucket == "weekly" else "D"
                chunks.append(f"df[{_fg(x)}] = pd.to_datetime(df[{_fg(x)}], errors='coerce', format='mixed')")
                chunks.append(f"df = df.dropna(subset=[{_fg(x)}])")
                chunks.append(f"df['_time_bucket'] = df[{_fg(x)}].dt.to_period({frequency!r}).astype(str)")
                x = "_time_bucket"

    # Filter pushdown: handle structured filters from VizSpec (in_top_n, last_n, in, between)
    # Case-insensitive column resolution: data_notes are lowercased while real
    # columns may be "GROSS AMT", so every membership test here resolves
    # through _rcol() instead of raw `in cols`.
    _cols_lower = {c.lower(): c for c in cols}
    def _rcol(name: str | None) -> str | None:
        if not name:
            return None
        if name in cols:
            return name
        hit = _cols_lower.get(name.lower())
        if hit:
            return hit
        try:
            from viz.resolve import normalize as _norm_res
            want = _norm_res(name)
            for c in cols:
                if _norm_res(c) == want:
                    return c
        except Exception:
            pass
        return None

    import re as _re2
    _x = _rcol(x)
    _y = _rcol(y)
    _group = _rcol(spec.group_by)
    # Top-N markers also come as plain "Top 10 by SUM(GROSS AMT)." (no
    # "filter:" prefix) from the deterministic planner — trigger on either.
    if notes and ("filter:" in notes or _re2.search(r"top\s+\d+\s+by\b", notes)):
        # Top-N pre-filter: e.g. Top 5 by revenue or filter: region in_top_n 5 rank_by SUM(revenue).
        # The rank capture allows SUM(...) with spaces ("SUM(GROSS AMT)").
        m_top = _re2.search(r"top\s+(\d+)\s+by\s+(SUM\s*\([^)]*\)|[^\s;,\.]+)", notes, re.IGNORECASE)
        if m_top:
            try:
                n_top = int(m_top.group(1))
                rank_col = m_top.group(2).strip().strip("'\"")
                # Resolve rank column name (strip SUM())
                rank_col_clean = _re2.sub(r".*\((.*)\).*", r"\1", rank_col) if "(" in rank_col else rank_col
                rank_col_clean = rank_col_clean.strip()
                _rank_col = _rcol(rank_col_clean)
                # Currency/text-stored rank columns ("$1,200") must be numeric
                # before groupby().sum(), otherwise pandas concatenates strings.
                if _rank_col:
                    chunks.append(f"df[{_fg(_rank_col)}] = pd.to_numeric(df[{_fg(_rank_col)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
                # For top-N, group by group_by if exists (e.g., top 5 Styles), otherwise x
                rank_group = _group if _group else _x
                # Also try to infer rank_group from filter text if x is temporal (month) but Style is intended
                if rank_group and _rank_col:
                    chunks.append(f"_rank = df.groupby({_fg(rank_group)})[{_fg(_rank_col)}].sum().nlargest({n_top}).index")
                    chunks.append(f"df = df[df[{_fg(rank_group)}].isin(_rank)]")
                elif rank_group:
                    # Rank by y if rank_col not found but y exists
                    y_for_rank = _rank_col or _y
                    if y_for_rank:
                        chunks.append(f"_rank = df.groupby({_fg(rank_group)})[{_fg(y_for_rank)}].sum().nlargest({n_top}).index")
                        chunks.append(f"df = df[df[{_fg(rank_group)}].isin(_rank)]")
                elif _rank_col and _y and _x:
                    chunks.append(f"_rank = df.groupby({_fg(_x)})[{_fg(_rank_col)}].sum().nlargest({n_top}).index")
                    chunks.append(f"df = df[df[{_fg(_x)}].isin(_rank)]")
                elif _rank_col and _x:
                    chunks.append(f"_rank = df.groupby({_fg(_x)})[{_fg(_rank_col)}].sum().nlargest({n_top}).index")
                    chunks.append(f"df = df[df[{_fg(_x)}].isin(_rank)]")
            except Exception:
                pass
        # Last N time filter: last 2 years / last_n 2 years
        if "last" in notes.lower() and x and x in cols:
            m_last = _re2.search(r"last\s+(\d+)\s+years?", notes, re.IGNORECASE)
            if m_last:
                try:
                    n_years = int(m_last.group(1))
                    chunks.append(f"df[{_fg(x)}] = pd.to_datetime(df[{_fg(x)}], errors='coerce')")
                    chunks.append(f"df = df.dropna(subset=[{_fg(x)}])")
                    chunks.append(f"_cutoff = df[{_fg(x)}].max() - pd.DateOffset(years={n_years})")
                    chunks.append(f"df = df[df[{_fg(x)}] >= _cutoff]")
                except Exception:
                    pass
        # Conditional: in list like quarter in ['Q1','Q4'] or year in [2023,2024]
        # Handle generic spec.filter as well
    if spec.filter:
        cond = _translate_filter(spec.filter, df)
        if cond:
            chunks.append(f"df = df[{cond[0]} {cond[1]}]")
    if "filter shipped orders" in notes:
        status_col = next((c for c in cols if c.lower() == "status"), None)
        if status_col:
            chunks.append(
                f"df = df[df[{_fg(status_col)}].astype(str).str.contains('shipped', case=False, na=False)]"
            )

    # --- data_notes: split / explode ---
    if "split" in notes or "comma" in notes or "explode" in notes:
        xcol = x or cols[0]
        chunks.append(
            f"df = df[df[{_fg(xcol)}].notna()].copy()"
        )
        chunks.append(
            f"df[{_fg(xcol)}] = df[{_fg(xcol)}].astype(str).str.split(',')"
        )
        chunks.append(f"df = df.explode({_fg(xcol)})")
        chunks.append(f"df[{_fg(xcol)}] = df[{_fg(xcol)}].str.strip()")
        chunks.append(f"df = df[df[{_fg(xcol)}] != '']")

    # --- data_notes: count distinct / nunique ---
    use_nunique = ("count distinct" in notes or "nunique" in notes or "unique" in notes
                   or agg == "count_distinct")

    if spec.chart_type == "scatter":
        xs, ys = x or cols[0], y or (cols[1] if len(cols) > 1 else cols[0])
        chunks.append(f"df[{_fg(xs)}] = pd.to_numeric(df[{_fg(xs)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
        chunks.append(f"result = df[[{_fg(xs)}, {_fg(ys)}]].dropna()")
        return "\n".join(chunks)

    if spec.chart_type in ("line", "area"):
        xcol = x or cols[0]
        if agg == "count":
            keys = [xcol] + ([spec.group_by] if spec.group_by and spec.group_by in cols else [])
            chunks.append(f"result = df.groupby({keys!r}).size().reset_index(name='count')")
            return "\n".join(chunks)
        if derived_margin:
            chunks.append(f"_m = df.groupby({_fg(xcol)}, as_index=False)[[{_fg(margin_sales)}, {_fg(margin_cost)}]].sum()")
            chunks.append(f"_m[{_fg(y)}] = ((_m[{_fg(margin_sales)}] - _m[{_fg(margin_cost)}]) / _m[{_fg(margin_sales)}].replace(0, pd.NA) * 100).fillna(0)")
            chunks.append(f"result = _m[[{_fg(xcol)}, {_fg(y)}]]")
            return "\n".join(chunks)
        if use_nunique and y and y in cols:
            if spec.group_by and spec.group_by in cols:
                chunks.append(
                    f"result = df.groupby([{_fg(xcol)}, {_fg(spec.group_by)}])"
                    f"[{_fg(y)}].nunique().reset_index(name='count')"
                )
            else:
                chunks.append(
                    f"result = df.sort_values({_fg(xcol)}).groupby({_fg(xcol)})"
                    f"[{_fg(y)}].nunique().reset_index(name='count')"
                )
        elif spec.group_by and spec.group_by in cols:
            chunks.append(
                f"result = df.groupby([{_fg(xcol)}, {_fg(spec.group_by)}])"
                f"[{_fg(y)}].agg('{agg}').reset_index()"
            )
        else:
            chunks.append(
                f"result = df.sort_values({_fg(xcol)}).groupby({_fg(xcol)})"
                f"[{_fg(y)}].agg('{agg}').reset_index()"
            )
        return "\n".join(chunks)

    if spec.chart_type == "histogram":
        col = x or y or cols[0]
        # Handle string numeric like GROSS AMT with $, commas
        chunks.append(f"df[{_fg(col)}] = pd.to_numeric(df[{_fg(col)}].astype(str).str.replace(r'[\\$,%]', '', regex=True).str.strip(), errors='coerce')")
        chunks.append(f"df = df.dropna(subset=[{_fg(col)}])")
        chunks.append(f"result = df[[{_fg(col)}]].dropna()")
        return "\n".join(chunks)

    if spec.chart_type == "boxplot":
        ycol = y or x or cols[0]
        if spec.x and spec.y and spec.x != spec.y:
            # group + measure
            chunks.append(f"result = df[[{_fg(spec.x)}, {_fg(spec.y)}]].dropna()")
        else:
            chunks.append(f"result = df[[{_fg(ycol)}]].dropna()")
        return "\n".join(chunks)

    if spec.chart_type == "heatmap":
        # Return numeric subset for correlation heatmap
        chunks.append(f"result = df.select_dtypes(include=['number'])")
        return "\n".join(chunks)

    # bar / horizontal_bar / pie / donut / grouped / stacked
    xcol = x or cols[0]
    if derived_margin:
        chunks.append(f"_m = df.groupby({_fg(xcol)}, as_index=False)[[{_fg(margin_sales)}, {_fg(margin_cost)}]].sum()")
        chunks.append(f"_m[{_fg(y)}] = ((_m[{_fg(margin_sales)}] - _m[{_fg(margin_cost)}]) / _m[{_fg(margin_sales)}].replace(0, pd.NA) * 100).fillna(0)")
        chunks.append(f"result = _m[[{_fg(xcol)}, {_fg(y)}]]")
        return "\n".join(chunks)
    # grouped / stacked need group_by handling
    if spec.chart_type in ("grouped_bar", "stacked_bar", "stacked_100"):
        gcol = spec.group_by
        if gcol and gcol in cols:
            if use_nunique and y and y in cols:
                chunks.append(f"result = df.groupby([{_fg(xcol)}, {_fg(gcol)}])[{_fg(y)}].nunique().reset_index(name='count')")
            elif agg == "count" or not y:
                chunks.append(f"result = df.groupby([{_fg(xcol)}, {_fg(gcol)}]).size().reset_index(name='count')")
            else:
                chunks.append(f"result = df.groupby([{_fg(xcol)}, {_fg(gcol)}])[[{_fg(y)}]].agg('{agg}').reset_index()")
        else:
            # Fallback to single x
            if use_nunique and y and y in cols:
                chunks.append(f"result = df.groupby({_fg(xcol)})[{_fg(y)}].nunique().reset_index(name='count')")
            elif agg == "count" or not y:
                chunks.append(f"result = df.groupby({_fg(xcol)}).size().reset_index(name='count')")
            else:
                chunks.append(f"result = df.groupby({_fg(xcol)})[[{_fg(y)}]].agg('{agg}').reset_index()")
    else:
        if use_nunique and y and y in cols:
            chunks.append(
                f"result = df.groupby({_fg(xcol)})[{_fg(y)}].nunique().reset_index(name='count')"
            )
        elif agg == "count" or not y:
            chunks.append(f"result = df.groupby({_fg(xcol)}).size().reset_index(name='count')")
        else:
            chunks.append(
                f"result = df.groupby({_fg(xcol)})[[{_fg(y)}]].agg('{agg}').reset_index()"
            )

    # --- data_notes: top N ---
    import re as _re
    m = _re.search(r"top\s+(\d+)", notes)
    if m and not (spec.group_by and re.search(r"top\s+\d+\s+by\b", notes)):
        n = int(m.group(1))
        chunks.append(f"result = result.sort_values(result.columns[-1], ascending=False).head({n})")

    return "\n".join(chunks)


def _codegen_llm(spec: ChartSpec, profile: SheetProfile, feedback: str | None = None) -> str:
    user = (
        f"Sheet schema:\n{inspect_data.inspect_text(profile)}\n\n"
        f"ChartSpec:\n{spec.model_dump_json(indent=2)}\n"
    )
    if feedback:
        user += f"\nPrevious attempt feedback (fix the code):\n{feedback}\n"
    user += "\nWrite the snippet.\n"
    try:
        out = llm_structured(AGENT_SYSTEM_PROMPT, user, CodeResult)
        return out.code
    except LLMError:
        return _codegen_deterministic(spec, _empty_df_for(profile))


def _empty_df_for(profile: SheetProfile) -> pd.DataFrame:
    return pd.DataFrame(columns=[c.name for c in profile.columns])


_QUOTED_COL_RE = re.compile(r"[`'\"\\]([^`'\"\\]+)[`'\"\\]")
_METRIC_VERBS_RE = re.compile(
    r"(?:change|switch|set|show|plot|display|sum|total|use|aggregate)\s+"
    r"(?:the\s+)?(?:metric|measure|measures|values?|total|sum|y[\s-]?axis\s+)?"
    r"(?:to|as|of)?\s*[`'\"\\]?([\w\s$%]{2,40}?)[`'\"\\]?\s*$",
    re.IGNORECASE,
)
_DIM_VERBS_RE = re.compile(
    r"(?:group\s+by|grouped\s+by|break\s+down\s+by|across|per|versus|\bvs\b|\bby\b)\s+"
    r"(?:the\s+)?[`'\"\\]?([\w\s$%]{2,40}?)[`'\"\\]?\s*$",
    re.IGNORECASE,
)
_TRAILING_CLAUSE_RE = re.compile(
    r"\s+(instead.*|per\s+.*|by\s+.*|for\s+.*|across\s+.*|as\s+(?:a\s+)?(?:metric|measure|dimension).*)$",
    re.IGNORECASE,
)


def _strip_trailing_clause(phrase: str) -> str:
    return _TRAILING_CLAUSE_RE.sub("", (phrase or "").strip()).strip().strip("`'\"\\ ")


def _apply_column_change(
    spec: ChartSpec,
    message: str,
    column_names: list[str] | None = None,
    role_map: dict[str, str] | None = None,
) -> tuple[ChartSpec, str] | None:
    """Metric/dimension change via the single resolver ("sum PCS instead").

    Returns (new_spec, note) when the message names a real column with the
    expected role and it differs from the current spec; else None (caller
    falls through to the "no edit" reply). Quoted names win over verb
    heuristics; role guards keep "group by Style" (dimension) and
    "sum PCS" (metric) from cross-applying.
    """
    if not column_names:
        return None
    role_map = role_map or {}
    try:
        from viz.resolve import resolve_column as _rc
    except Exception:
        return None

    metric_cands: list[str] = []
    dim_cands: list[str] = []
    for q in _QUOTED_COL_RE.findall(message):
        q = q.strip()
        if q:
            metric_cands.append(q)
            dim_cands.append(q)
    mm = _METRIC_VERBS_RE.search(message)
    if mm and mm.group(1).strip():
        metric_cands.append(_strip_trailing_clause(mm.group(1)))
    dm = _DIM_VERBS_RE.search(message)
    if dm and dm.group(1).strip():
        dim_cands.append(_strip_trailing_clause(dm.group(1)))

    new_spec = spec.model_copy(deep=True)
    notes: list[str] = []

    for cand in metric_cands:
        if not cand:
            continue
        col, score = _rc(cand, column_names, role_map, prefer_role="numeric")
        if col and score >= 1.0 and role_map.get(col, "numeric") == "numeric" and col != spec.y:
            new_spec.y = col
            if (new_spec.agg_function or "sum") == "count":
                new_spec.agg_function = "sum"
            notes.append(f"Changed metric to '{col}'.")
            break

    for cand in dim_cands:
        if not cand:
            continue
        col, score = _rc(cand, column_names, role_map, prefer_role="categorical")
        if (col and score >= 1.0 and role_map.get(col) in ("categorical", "temporal")
                and col != new_spec.x and col != new_spec.y):
            new_spec.x = col
            new_spec.group_by = None  # avoid stale grouping on the old dimension
            notes.append(f"Changed dimension to '{col}'.")
            break

    if not notes:
        return None
    return new_spec, " ".join(notes)


def _apply_edit(
    spec: ChartSpec,
    message: str,
    known_categories: list[str] | None = None,
    column_names: list[str] | None = None,
    role_map: dict[str, str] | None = None,
) -> tuple[ChartSpec, str]:
    """Interpret a follow-up message as a targeted edit to a chart spec."""
    msg = message.lower().strip()

    rename_m = re.search(
        r"(?:rename|label|call)\s+(.+?)\s+(?:to|as)\s+(.+?)\s*$",
        message,
        re.IGNORECASE,
    )
    if rename_m:
        old_name = rename_m.group(1).strip().strip('"').strip("'")
        new_name = rename_m.group(2).strip().strip('"').strip("'")
        if known_categories:  # resolve the real casing from the chart's actual names
            for known in known_categories:
                if known.lower() == old_name.lower():
                    old_name = known
                    break
        if old_name and new_name and new_name.lower() != old_name.lower():
            new_spec = spec.model_copy(deep=True)
            new_spec.label_map = dict(spec.label_map or {})
            new_spec.label_map[old_name] = new_name
            return new_spec, f"Labeled '{old_name}' as '{new_name}'."
        return spec, "No change: the label was left as is."

    mapping = {
        "100% stacked bar": "stacked_100",
        "100% stacked": "stacked_100",
        "stacked bar": "stacked_bar",
        "grouped bar": "grouped_bar",
        "horizontal bar": "horizontal_bar",
        "bar chart": "bar",
        "line chart": "line",
        "area chart": "area",
        "area": "area",
        "histogram": "histogram",
        "box plot": "boxplot",
        "boxplot": "boxplot",
        "heatmap": "heatmap",
        "donut": "donut",
        "pie": "pie",
        "scatter": "scatter",
        "line": "line",
        "bar": "bar",
    }
    changed = None
    for phrase, ctype in mapping.items():
        if phrase in msg:
            changed = ctype
            break
    if changed and changed != spec.chart_type:
        new_spec = spec.model_copy(deep=True)
        new_spec.chart_type = changed
        return new_spec, f"Changed chart type to '{changed}'."

    if "merge" in msg and ("tail" in msg or "other" in msg):
        if spec.show_tail_categories:
            new_spec = spec.model_copy(deep=True)
            new_spec.show_tail_categories = False
            return new_spec, "Merged the long tail back into 'other' (top 10 shown)."
        return spec, "The long tail is already merged into 'other'."

    if ("label" in msg or "name" in msg) and ("group" in msg or "real" in msg):
        if not spec.show_tail_categories:
            new_spec = spec.model_copy(deep=True)
            new_spec.show_tail_categories = True
            return new_spec, "Showing every category under its real name (long-tail 'other' removed)."
        return spec, "Every category already shows its real name."

    col_changed = _apply_column_change(spec, message, column_names, role_map)
    if col_changed is not None:
        return col_changed

    return spec, (
        "No edit applied. I can change the chart type (e.g. 'make it a bar'), "
        "the metric (e.g. 'sum PCS instead'), the dimension (e.g. 'group by Style'), "
        "rename a label (e.g. 'rename other to group'), or show real category "
        "names ('show real names' / 'labels to group')."
    )


def resolve_edit(message: str, results: Sequence[ChartResult]) -> tuple[ChartResult | None, str]:
    """Find the ChartResult this message is about (best word overlap on titles)."""
    msg_tokens = set(re.findall(r"[a-z0-9]+", message.lower()))
    best_idx, best_score = None, 0
    for idx, r in enumerate(results):
        t = set(re.findall(r"[a-z0-9]+", r.spec.title.lower())) | {r.spec.chart_type}
        score = len(msg_tokens & t)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is None or best_score == 0:
        return None, "Could not identify which chart you mean; try mentioning its title or a column."
    return results[best_idx], "matched"


def _friendly_execution_error(error: str | None, *, blocked: bool = False, timed_out: bool = False) -> tuple[str, str]:
    """Convert internal sandbox failures into safe, actionable user feedback."""
    if blocked:
        return "security_block", "The chart calculation was blocked by the sandbox for safety."
    if timed_out:
        return "timeout", "The chart calculation took too long and was stopped. Try a simpler request."
    text = (error or "").strip()
    lower = text.lower()
    if "keyerror" in lower or "not found in the raw frame" in lower:
        return "missing_column", "The chart calculation referenced a column that is not available in the dataset."
    if "syntaxerror" in lower or "does not parse" in lower:
        return "invalid_code", "The chart calculation could not be parsed. Please rephrase the request."
    if "typeerror" in lower or "valueerror" in lower or "cannot convert" in lower:
        return "invalid_calculation", "The requested calculation could not be applied to the selected data."
    if "empty result" in lower:
        return "empty_result", "The calculation returned no data. Try a broader request or check the filters."
    return "execution_error", "The chart calculation failed. Try rephrasing the request or choosing another measure."


def execute_spec(spec: ChartSpec, workbook: Workbook, attempt_llm: bool = True) -> ChartResult:
    if spec.status == "skipped":
        return ChartResult(spec=spec)

    profile = workbook.profile_for(spec.sheet)
    df = workbook.frames.get(spec.sheet)
    if df is None or df.empty:
        return ChartResult(
            spec=spec,
            adaptation_note=f"Sheet '{spec.sheet}' has no data to execute.",
        )

    # --- observe -------------------------------------------------------------
    _ = inspect_data.inspect(profile)  # tool call #1: schema observation

    # --- data-quality pre-scan (host-side, exact counts for the notes) -------
    try:
        _qrep = quarantine_report(df, spec.x)
    except Exception:
        _qrep = {"count": 0, "examples": []}
    try:
        _blanks = blank_row_count(df)
    except Exception:
        _blanks = 0

    last_error: str | None = None
    last_verification: dict = {}
    last_category = "execution_error"
    last_blocked = False
    last_timed_out = False
    feedback: str | None = None
    # Snapshot the planned x: deterministic codegen may rewrite spec.x to a
    # derived `_time_bucket` column for chart building. Verification must run
    # against the ORIGINAL column (it re-derives the bucket itself); using
    # the mutated spec makes every bucketed chart fail with
    # "Category column '_time_bucket' not found in the raw frame."
    _verify_x = spec.x
    for attempt in range(MAX_ATTEMPTS):
        # --- think: write (or rewrite) the pandas snippet --------------------
        # Option A contract: deterministic codegen runs FIRST (reproducible,
        # no concat/merge inventions). The LLM is a fallback for a failed
        # deterministic attempt only, and attempt_llm=False disables it
        # entirely. Either way the snippet is sandboxed + verified.
        if attempt == 0 or not attempt_llm:
            code = _codegen_deterministic(spec, df)
        else:
            code = _codegen_llm(spec, profile, feedback)

        # --- act: sandboxed execution ----------------------------------------
        run_result = run_code.run_snippet(df, code)  # tool call #2
        if not run_result.ok:
            last_error = run_result.error
            last_blocked = run_result.blocked
            last_timed_out = run_result.timed_out
            last_category, _ = _friendly_execution_error(
                run_result.error, blocked=run_result.blocked, timed_out=run_result.timed_out
            )
            feedback = (
                "Security block: the code is not allowed. Rewrite it with a safe, "
                "pure-pandas approach."
            ) if run_result.blocked else (
                f"Runtime error (attempt {attempt + 1}): {run_result.error}"
            )
            if run_result.blocked:
                break  # a blocked snippet is not something to retry
            continue

        built_df = run_code.reconstruct_df(run_result)
        if built_df is None or built_df.empty or len(built_df) == 0:
            last_error = "The snippet produced an empty result."
            last_category, _ = _friendly_execution_error(last_error)
            feedback = f"Empty result (attempt {attempt + 1}). Produce an aggregated DataFrame."
            continue

        # --- act: build the chart ---------------------------------------------
        try:
            # Time buckets are derived inside the sandbox. Build against a
            # temporary render spec while returning the original source
            # column in the stored ChartSpec.
            render_spec = spec
            if spec.x not in built_df.columns and "_time_bucket" in built_df.columns:
                render_spec = spec.model_copy()
                render_spec.x = "_time_bucket"
            chart = create_chart.build_chart(render_spec, built_df)  # tool call #3
        except ChartBuildError as exc:
            last_error = str(exc)
            last_category = "chart_build_error"
            feedback = f"Chart build failed (attempt {attempt + 1}): {exc}"
            continue

        # --- verify: independent closed-form recomputation against the raw frame
        # Use the pre-mutation x (see _verify_x): codegen may have rewritten
        # spec.x to a derived bucket column that exists only in built_df.
        _verify_spec = spec
        if spec.x != _verify_x:
            _verify_spec = spec.model_copy()
            _verify_spec.x = _verify_x
        verified, verification = create_chart.verify_computed(_verify_spec, df, chart.computed_summary, computed_df=built_df)
        if not verified and verification.get("scope") == "all_groups":
            last_verification = verification
            last_error = "Computed chart values did not match independent source-data verification."
            last_category = "verification_failed"
            feedback = last_error + " " + str(verification)
            continue

        # --- semantic validation: verify result logic ---
        validation_result = validate_chart(render_spec, df, built_df, chart.computed_summary)

        # --- recommendations: generate related chart specs (not executed yet) ---
        profile = workbook.profile_for(spec.sheet)
        rec_specs = recommend_charts(spec, profile)
        rec_results = [ChartResult(spec=rs) for rs in rec_specs[1:]]  # specs only, not executed

        # --- data-quality notes: what was excluded, with counts -------------
        dq_notes: list[str] = []
        if _qrep.get("count"):
            _ex = ", ".join(f"'{e}'" for e in (_qrep.get("examples") or [])[:3])
            dq_notes.append(
                f"Excluded {_qrep['count']} misaligned month-like values from "
                f"'{spec.x}' (e.g. {_ex}) — source rows stored under the wrong column."
            )
        if _blanks:
            dq_notes.append(f"Ignored {_blanks} fully-blank source rows.")
        _adapt = chart.adaptation_note or ""
        if dq_notes:
            _adapt = (_adapt + " " if _adapt else "") + " ".join(dq_notes)

        return ChartResult(
            spec=spec,
            figure_json=chart.figure_json,
            computed_summary=chart.computed_summary,
            figure_data=chart.figure_data,
            adaptation_note=_adapt or None,
            verified=verified,
            verification=verification,
            recommendations=rec_results,
            validation=validation_result.to_dict(),
        )

    category, note = _friendly_execution_error(
        last_error, blocked=last_blocked, timed_out=last_timed_out
    )
    if last_category in ("chart_build_error", "verification_failed"):
        category = last_category
    if last_category == "verification_failed":
        note = "The calculated chart did not match the source data and was withheld. Please refine the request."
    return ChartResult(
        spec=spec,
        adaptation_note=note,
        execution_error=last_error,
        execution_error_category=category or last_category,
        verification=last_verification,
    )


def execute_plan(
    workbook: Workbook,
    specs: Sequence[ChartSpec],
    attempt_llm: bool = True,
    progress=None,
) -> list[ChartResult]:
    """Execute every chart spec through the tool loop.

    `progress` is an optional callable ``progress(done, total, spec)`` invoked
    before each chart for live UI feedback.
    """
    total = len(specs)
    results: list[ChartResult] = []
    for i, spec in enumerate(specs, start=1):
        if progress is not None:
            progress(i, total, spec)
        results.append(execute_spec(spec, workbook, attempt_llm=attempt_llm))
    return results


def reexecute_spec(
    workbook: Workbook,
    index: int,
    results: list[ChartResult],
    message: str,
    attempt_llm: bool = True,
) -> tuple[list[ChartResult], str]:
    """Route a follow-up chat message into the agent loop for one chart only."""
    if not (0 <= index < len(results)):
        return results, "Chart index out of range."
    target = results[index]
    if target.skipped:
        return results, "That chart was skipped during planning; no execution to edit."
    known = [str(c.get("category")) for c in (target.computed_summary or {}).get("top_categories", [])]
    key = (target.computed_summary or {}).get("grouped_by")
    if key and target.figure_data:
        for row in target.figure_data:
            v = row.get(key)
            if v is not None and not isinstance(v, bool):
                known.append(str(v))
    known = list(dict.fromkeys(known))
    # Column inventory + live roles so refine can change metric/dimension
    # ("sum PCS instead", "group by Style") via the single resolver.
    column_names: list[str] | None = None
    role_map: dict[str, str] | None = None
    try:
        from viz.profiler import profile_data as _profile_data

        _prof = workbook.profile_for(target.spec.sheet)
        _dprof = _profile_data(_prof, workbook.frames.get(target.spec.sheet))
        column_names = _dprof.column_names()
        role_map = {c.name: c.role for c in _dprof.columns}
    except Exception:
        try:
            _prof = workbook.profile_for(target.spec.sheet)
            column_names = [c.name for c in _prof.columns]
        except Exception:
            pass
    spec, note = _apply_edit(
        target.spec, message, known_categories=known,
        column_names=column_names, role_map=role_map,
    )
    new_result = execute_spec(spec, workbook, attempt_llm=attempt_llm)
    results = list(results)
    results[index] = new_result
    return results, f"{note}\nRe-executed chart: {spec.title}." if new_result.figure_json else f"{note}\nChart re-run failed: {new_result.adaptation_note}"
