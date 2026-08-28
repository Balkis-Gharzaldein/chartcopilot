"""create_chart tool: build a Plotly figure from a ChartSpec + computed data.

Applies sensible adaptation instead of crashing:
  * pie/bar with more than 10 categories -> top 10 + "other" (explicit note)
  * a pie on near-continuous data with no natural categories -> substitute a bar
  * missing/empty data -> ChartBuildError so the agent loop can react
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from schemas import ChartSpec

MAX_CATEGORIES = 10

CHARTS_WITH_CATEGORIES = {"pie", "bar", "horizontal_bar"}


class ChartBuildError(RuntimeError):
    pass


@dataclass
class BuiltChart:
    figure_json: str
    computed_summary: dict
    figure_data: list[dict]
    adaptation_note: str | None = None


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    return df.dropna(how="all").copy()


def _pick_xy(spec: ChartSpec, df: pd.DataFrame) -> tuple[str, str]:
    """Decide category (x) and value (y) column names for the given data."""
    x = (spec.x or "").strip()
    y = (spec.y or "").strip()
    cols = list(df.columns)
    if not x and cols:
        x = cols[0]
    # If y is specified but not present in the result DataFrame (e.g. codegen renamed
    # the aggregated column to 'count'), fall back to the best numeric column.
    if y and y not in df.columns and y != x:
        numeric = [c for c in cols if c != x and _to_numeric(df[c]).notna().mean() > 0.8]
        y = numeric[0] if numeric else (cols[-1] if len(cols) > 1 else y)
    elif not y and len(cols) > 1:
        # pick the most numeric-looking column after x
        numeric = [c for c in cols if c != x and _to_numeric(df[c]).notna().mean() > 0.8]
        y = numeric[0] if numeric else cols[-1]
    if not y and spec.agg_function == "count":
        return x, x
    return x, y


def _aggregate_if_raw(df: pd.DataFrame, x: str, y: str) -> tuple[pd.DataFrame, bool]:
    """Collapse repeated categories to one row per category; report if we did."""
    if x not in df.columns or y not in df.columns or y == x:
        return df, False
    if df[x].nunique(dropna=True) < len(df):
        tmp = df.copy()
        tmp[y] = _to_numeric(tmp[y])
        if tmp[y].notna().sum() == 0:
            return df, False
        grouped = tmp.dropna(subset=[y]).groupby(x, as_index=False)[y].sum()
        return grouped, True
    return df, False


def _bucket_long_tail(df: pd.DataFrame, x: str, y: str, keep_all: bool = False) -> tuple[pd.DataFrame, str | None]:
    ordered = df.sort_values(y, ascending=False).reset_index(drop=True)
    n = len(ordered)
    note = None
    if keep_all or n <= MAX_CATEGORIES:
        return ordered, None
    top = ordered.head(MAX_CATEGORIES).copy()
    other_val = ordered.iloc[MAX_CATEGORIES:][y].sum()
    bottom = pd.DataFrame([{x: "other", y: other_val}])
    note = f"Bucketed {n - MAX_CATEGORIES} categories into 'other' (top {MAX_CATEGORIES} shown)."
    return pd.concat([top, bottom], ignore_index=True), note


def _apply_labels(df: pd.DataFrame, x: str, y: str, label_map: dict[str, str] | None) -> pd.DataFrame:
    """Apply a category -> display-label rename, merging any categories that end up
    with the same label (values are summed) and re-sorting."""
    if not label_map:
        return df
    tmp = df.copy()
    tmp[x] = tmp[x].astype(str).map(lambda v: label_map.get(v, v))
    tmp = tmp.groupby(x, as_index=False)[y].sum()
    return tmp.sort_values(y, ascending=False).reset_index(drop=True)


def _is_continuous(series: pd.Series) -> bool:
    if not pd.api.types.is_numeric_dtype(series):
        return False
    nunique = series.nunique(dropna=True)
    if len(series) == 0:
        return False
    return nunique / len(series) > 0.3 and nunique > 15


def _rows(df: pd.DataFrame, cap: int = 500) -> list[dict]:
    """The exact rows a figure was built from (records), capped for the UI table."""
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_numeric_dtype(d[c]):
            d[c] = d[c].round(2)
    records = d.to_dict(orient="records")
    return records[:cap]


def _summary_for_categorical(x, y, df, agg) -> dict:
    total = float(df[y].sum())
    top = df.nlargest(5, y)[[x, y]].to_dict(orient="records")
    top = [{"category": str(r[x]), "value": round(float(r[y]), 2)} for r in top]
    dominant = float(top[0]["value"]) if top else 0.0
    return {
        "agg_function": agg,
        "measure": y,  # self-describing: what is being totalled
        "grouped_by": x,  # self-describing: what it is grouped across
        "total": round(total, 2),
        "n_categories": int(len(df)),
        "top_categories": top,
        "top_share": round(dominant / total, 4) if total else 0.0,
    }


def _summary_for_line(df, x, y) -> dict:
    d = df.dropna(subset=[y]).sort_values(x)
    vals = _to_numeric(d[y])
    total = float(vals.sum())
    first_v = float(vals.iloc[0]) if len(vals) else 0.0
    last_v = float(vals.iloc[-1]) if len(vals) else 0.0
    return {
        "chart_type": "line",
        "measure": y,
        "grouped_by": x,
        "points": int(len(d)),
        "first_value": round(first_v, 2),
        "last_value": round(last_v, 2),
        "change_pct": round((last_v - first_v) / first_v * 100, 1) if first_v != 0 else None,
        "total": round(total, 2),
    }


def _pearson(xs, ys) -> float | None:
    """Pearson correlation (works on Python <3.10 where statistics.correlation is absent)."""
    xs = [float(x) for x in xs]
    ys = [float(y) for y in ys]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    den_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _summary_for_scatter(df, x, y) -> dict:
    d = df.dropna(subset=[x, y])
    xs = _to_numeric(d[x])
    ys = _to_numeric(d[y])
    corr = None
    if len(d) >= 2 and xs.nunique() > 1 and ys.nunique() > 1:
        try:
            corr = round(_pearson(list(xs), list(ys)), 3)
        except (ValueError, TypeError, ZeroDivisionError):
            corr = None
    xv = _to_numeric(d[x])
    return {
        "chart_type": "scatter",
        "measure": y,
        "grouped_by": x,
        "points": int(len(d)),
        "x_min": round(float(xv.min()), 2) if len(xv) else None,
        "x_max": round(float(xv.max()), 2) if len(xv) else None,
        "y_min": round(float(ys.min()), 2) if len(ys) else None,
        "y_max": round(float(ys.max()), 2) if len(ys) else None,
        "correlation": corr,
    }


def build_chart(spec: ChartSpec, df: pd.DataFrame) -> BuiltChart:
    """Create (figure_json, computed_summary, adaptation_note) for a ChartSpec."""
    if df is None or df.empty:
        raise ChartBuildError("No data available to chart this spec.")
    df = _clean(df)
    x, y = _pick_xy(spec, df)
    if x not in df.columns:
        raise ChartBuildError(f"Column '{x}' not present in the computed result.")
    adaptation: list[str] = []

    if spec.chart_type in CHARTS_WITH_CATEGORIES:
        if y not in df.columns or y == x:
            if spec.chart_type == "horizontal_bar" and x in df.columns:
                # rank-style chart: use the category column itself as value counts
                counts = df[x].value_counts().reset_index()
                counts.columns = [x, "count"]
                df = counts
                y = "count"
            else:
                raise ChartBuildError(
                    f"Cannot chart '{spec.chart_type}': no value column available (spec y='{spec.y}')."
                )
        grouped, regrouped = _aggregate_if_raw(df, x, y)
        if regrouped:
            adaptation.append(f"Aggregated '{y}' by '{x}' ({spec.agg_function or 'sum'}).")
        # pie is meaningless on near-continuous/x data with no natural categories
        continuous_x = _is_continuous(grouped[x])
        if spec.chart_type == "pie" and continuous_x:
            spec_copy = spec.model_copy(deep=True)
            spec_copy.chart_type = "horizontal_bar"
            adaptation.append("Pie requested on a near-continuous field; substituted a bar chart.")
            return build_chart(spec_copy, df)

        bucketed, note = _bucket_long_tail(grouped, x, y, keep_all=spec.show_tail_categories)
        if note:
            adaptation.append(note)
        elif spec.show_tail_categories and len(grouped) > MAX_CATEGORIES:
            adaptation.append(
                f"Showing all {len(grouped)} categories under their real names (no 'other' merge)."
            )
        if spec.label_map:
            bucketed = _apply_labels(bucketed, x, y, spec.label_map)
            adaptation.append("Relabeled categories per the edit request.")

        if spec.chart_type == "pie":
            if (bucketed[y] < 0).any():
                raise ChartBuildError("Pie chart requires non-negative values.")
            fig = px.pie(bucketed, names=x, values=y, title=spec.title)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            return BuiltChart(
                figure_json=fig.to_json(),
                computed_summary=_summary_for_categorical(x, y, bucketed, spec.agg_function or "sum"),
                figure_data=_rows(bucketed),
                adaptation_note=" ".join(adaptation) or None,
            )

        if spec.chart_type == "horizontal_bar":
            ordered = bucketed.sort_values(y, ascending=True)
            fig = px.bar(ordered, x=y, y=x, orientation="h", title=spec.title)
            fig.update_layout(xaxis_title=y, yaxis_title=x)
        else:
            fig = px.bar(bucketed, x=x, y=y, title=spec.title)
            fig.update_layout(xaxis_title=x, yaxis_title=y)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_categorical(x, y, bucketed, spec.agg_function or "sum"),
            figure_data=_rows(bucketed),
            adaptation_note=" ".join(adaptation) or None,
        )

    if spec.chart_type == "line":
        if y not in df.columns or y == x:
            raise ChartBuildError(f"Cannot chart 'line': no value column (spec y='{spec.y}').")
        d = df.sort_values(x)
        if spec.group_by and spec.group_by in d.columns:
            fig = px.line(d, x=x, y=y, color=spec.group_by, title=spec.title)
        else:
            fig = go.Figure(go.Scatter(x=d[x], y=_to_numeric(d[y]), mode="lines+markers", name=y))
            fig.update_layout(title=spec.title, xaxis_title=x, yaxis_title=y)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_line(d, x, y),
            figure_data=_rows(d),
            adaptation_note=None,
        )

    if spec.chart_type == "scatter":
        d = df.dropna(subset=[x, y])
        if not spec.y or spec.y not in d.columns or not spec.x or spec.x not in d.columns:
            raise ChartBuildError("Scatter chart requires both x and y columns.")
        fig = px.scatter(d, x=spec.x, y=spec.y, title=spec.title)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_scatter(d, x, y),
            figure_data=_rows(d),
            adaptation_note=None,
        )

    raise ChartBuildError(f"Unsupported chart type: {spec.chart_type}.")


def verify_computed(spec: ChartSpec, raw_df: pd.DataFrame, summary: dict) -> tuple[bool, dict]:
    """Independently recompute the headline numbers from the RAW input frame (a second,
    sandbox-free code path) and compare them with computed_summary.

    Returns (verified, checks). If the data can't be cleanly recomputed (filters/joins),
    the check degrades to a documented shape check instead of a silent pass.
    """
    checks: dict[str, dict] = {}

    def cmp(ok: bool, expected, found, tol: float = 0.01) -> dict:
        return {"expected": expected, "found": found, "ok": bool(ok), "tolerance": tol}

    if raw_df is None or raw_df.empty or len(raw_df) == 0:
        return False, {"error": "No raw frame available to verify against."}

    x = (spec.x or "").strip() or (list(raw_df.columns)[0] if len(raw_df.columns) else "")
    y = (spec.y or "").strip()
    if not y and spec.chart_type in CHARTS_WITH_CATEGORIES:
        y = x
    if x not in raw_df.columns:
        return False, {"error": f"Category column '{x}' not found in the raw frame."}

    agg = (spec.agg_function or "sum").lower()
    notes = (spec.data_notes or "").lower()
    needs_split = "split" in notes or "comma" in notes or "explode" in notes
    if spec.chart_type in CHARTS_WITH_CATEGORIES:
        g = raw_df.copy()
        if agg == "count" or (not y and agg != "count_distinct"):
            recomputed = g.groupby(x).size().reset_index(name="count")
            recomputed.rename(columns={x: "cat"}, inplace=True)
            value_col = "count"
        elif agg == "count_distinct":
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found in the raw frame for count_distinct verification."}
            if needs_split:
                g = g[g[x].notna()].copy()
                g[x] = g[x].astype(str).str.split(",")
                g = g.explode(x)
                g[x] = g[x].str.strip()
                g = g[g[x] != ""]
            recomputed = g.groupby(x)[y].nunique().reset_index(name="count")
            recomputed.rename(columns={x: "cat"}, inplace=True)
            value_col = "count"
        else:
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found in the raw frame."}
            g[y] = pd.to_numeric(g[y], errors="coerce")
            g = g.dropna(subset=[y])
            recomputed = g.groupby(x, as_index=False)[y].sum()
            value_col = y
        ordered = recomputed.sort_values(value_col, ascending=False).reset_index(drop=True)
        total = float(ordered[value_col].sum())
        keep_all = bool(getattr(spec, "show_tail_categories", False))
        bucket_count = None if keep_all else MAX_CATEGORIES
        if bucket_count and len(ordered) > bucket_count:
            other_val = float(ordered.iloc[bucket_count:][value_col].sum())
        else:
            other_val = 0.0
        checks["total"] = cmp(abs(total - float(summary.get("total", 0))) <= 0.01, total, summary.get("total"))
        label_map = spec.label_map or {}
        cat_col = "cat" if agg in ("count", "count_distinct") else x

        def display(v: str) -> str:
            return label_map.get(str(v), str(v))

        display_sums: dict[str, float] = {}
        for _, r in ordered.iterrows():
            d = display(str(r[cat_col]))
            display_sums[d] = display_sums.get(d, 0.0) + float(r[value_col])
        found_cats = summary.get("top_categories", [])
        mismatched = []
        found_other = None
        for r in found_cats:
            c = str(r.get("category"))
            v = float(r.get("value", 0))
            if c == "other":
                found_other = v
                continue
            if c not in display_sums or abs(display_sums[c] - v) > 0.01:
                mismatched.append(c)
        checks["top_categories_match"] = {
            "ok": not mismatched,
            "expected": [display(str(v)) for v in ordered.head(5)[cat_col].tolist()],
            "found": [r.get("category") for r in found_cats],
            "mismatched": mismatched,
            "tolerance": 0.01,
        }
        if bucket_count is None:
            checks["other_bucket"] = cmp(found_other is None, "no 'other' slice", found_other)
        else:
            checks["other_bucket"] = cmp(abs(other_val - float(found_other or 0)) <= 0.01, other_val, found_other)
    elif spec.chart_type == "line":
        if y not in raw_df.columns:
            return False, {"error": f"Value column '{y}' not found in the raw frame."}
        g = raw_df.copy()
        g[y] = pd.to_numeric(g[y], errors="coerce")
        g = g.dropna(subset=[y])
        monthly = g.groupby(x, as_index=False)[y].sum().sort_values(x)
        total = float(monthly[y].sum())
        checks["total"] = cmp(abs(total - float(summary.get("total", 0))) <= 0.01, total, summary.get("total"))
        if not spec.group_by:
            first_v = float(monthly[y].iloc[0]) if len(monthly) else 0.0
            last_v = float(monthly[y].iloc[-1]) if len(monthly) else 0.0
            checks["first_value"] = cmp(abs(first_v - float(summary.get("first_value", 0))) <= 0.01, first_v, summary.get("first_value"))
            checks["last_value"] = cmp(abs(last_v - float(summary.get("last_value", 0))) <= 0.01, last_v, summary.get("last_value"))
    elif spec.chart_type == "scatter":
        xs = spec.x or (list(raw_df.columns)[0] if len(raw_df.columns) else "")
        ys = spec.y or next(
            (c for c in raw_df.columns if c != xs and pd.api.types.is_numeric_dtype(raw_df[c])),
            None,
        )
        if not xs or not ys or xs not in raw_df.columns or ys not in raw_df.columns:
            return False, {"error": "Scatter requires both x and y in the raw frame."}
        d = raw_df[[xs, ys]].dropna()
        xs_vals = pd.to_numeric(d[xs], errors="coerce").tolist()
        ys_vals = pd.to_numeric(d[ys], errors="coerce").tolist()
        recomputed_corr = None
        if len(xs_vals) >= 2 and len(set(xs_vals)) > 1 and len(set(ys_vals)) > 1:
            recomputed_corr = round(_pearson(xs_vals, ys_vals), 3)
        expected_corr = summary.get("correlation")
        checks["points"] = cmp(summary.get("points") == len(xs_vals), len(xs_vals), summary.get("points"))
        checks["correlation"] = cmp(
            recomputed_corr is not None and expected_corr is not None
            and abs(recomputed_corr - float(expected_corr)) <= 0.001,
            recomputed_corr, expected_corr, tol=0.001,
        )
    else:
        return False, {"error": f"Verification not implemented for chart type: {spec.chart_type}"}

    failed = [name for name, c in checks.items() if not c.get("ok")]
    return not failed, {"checks": checks, "failed": failed}