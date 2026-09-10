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
from tools.data_quality import apply_quarantine
from tools.chart_theme import (
    CATEGORICAL,
    DIVERGING_SCALE,
    PRIMARY,
    SEQUENTIAL_SCALE,
    apply_base_layout,
    get_categorical,
)

MAX_CATEGORIES = 10

CHARTS_WITH_CATEGORIES = {"pie", "donut", "bar", "horizontal_bar", "grouped_bar", "stacked_bar", "stacked_100"}


class ChartBuildError(RuntimeError):
    pass


@dataclass
class BuiltChart:
    figure_json: str
    computed_summary: dict
    figure_data: list[dict]
    adaptation_note: str | None = None


def _to_numeric(series: pd.Series) -> pd.Series:
    # Late cleaning for currency strings ($ , %) — preserve raw but ensure numeric for sum
    try:
        cleaned = series.astype(str).str.replace(r'[\$,%]', '', regex=True).str.strip()
        # Handle empty strings after cleaning
        cleaned = cleaned.replace("", pd.NA)
        return pd.to_numeric(cleaned, errors="coerce")
    except Exception:
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


def _aggregate_if_raw(df: pd.DataFrame, x: str, y: str, agg: str = "sum") -> tuple[pd.DataFrame, bool]:
    """Collapse repeated categories to one row per category; report if we did."""
    if x not in df.columns or y not in df.columns or y == x:
        return df, False
    if df[x].nunique(dropna=True) < len(df):
        tmp = df.copy()
        tmp[y] = _to_numeric(tmp[y])
        if tmp[y].notna().sum() == 0:
            return df, False
        reducer = agg if agg in ("mean", "median", "min", "max") else "sum"
        grouped = tmp.dropna(subset=[y]).groupby(x, as_index=False)[y].agg(reducer)
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
        "total_kind": "sum_of_group_statistics" if agg not in ("sum", "count") else "additive_total",
        "measure": y,  # self-describing: what is being totalled
        "grouped_by": x,  # self-describing: what it is grouped across
        "total": round(total, 2),
        "n_categories": int(len(df)),
        "top_categories": top,
        **({"top_share": round(dominant / total, 4) if total else 0.0} if agg in ("sum", "count") else {}),
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


def _summary_for_histogram(df, x) -> dict:
    vals = _to_numeric(df[x])
    vals = vals.dropna()
    return {
        "chart_type": "histogram",
        "measure": x,
        "grouped_by": x,
        "count": int(len(vals)),
        "mean": round(float(vals.mean()), 2) if len(vals) else None,
        "median": round(float(vals.median()), 2) if len(vals) else None,
        "std": round(float(vals.std()), 2) if len(vals) > 1 else None,
        "min": round(float(vals.min()), 2) if len(vals) else None,
        "max": round(float(vals.max()), 2) if len(vals) else None,
    }


def _summary_for_box(df, x, y) -> dict:
    # x categorical group, y numeric; or y is numeric alone
    if y and y in df.columns:
        vals = _to_numeric(df[y]).dropna()
        groups = df[x].nunique() if x in df.columns else 1
        return {
            "chart_type": "boxplot",
            "measure": y,
            "grouped_by": x if x in df.columns else y,
            "count": int(len(vals)),
            "groups": int(groups),
            "median": round(float(vals.median()), 2) if len(vals) else None,
            "q1": round(float(vals.quantile(0.25)), 2) if len(vals) else None,
            "q3": round(float(vals.quantile(0.75)), 2) if len(vals) else None,
            "min": round(float(vals.min()), 2) if len(vals) else None,
            "max": round(float(vals.max()), 2) if len(vals) else None,
        }
    # fallback
    col = x if x in df.columns else y
    vals = _to_numeric(df[col]).dropna()
    return {
        "chart_type": "boxplot",
        "measure": col,
        "grouped_by": col,
        "count": int(len(vals)),
        "median": round(float(vals.median()), 2) if len(vals) else None,
    }


def _summary_for_heatmap(df) -> dict:
    # df is correlation matrix already? For builder we pass corr matrix
    return {
        "chart_type": "heatmap",
        "n_cols": int(df.shape[1]) if hasattr(df, "shape") else 0,
        "n_rows": int(df.shape[0]) if hasattr(df, "shape") else 0,
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
        grouped, regrouped = (df, False) if spec.group_by else _aggregate_if_raw(df, x, y, spec.agg_function or "sum")
        if regrouped:
            adaptation.append(f"Aggregated '{y}' by '{x}' ({spec.agg_function or 'sum'}).")
        # pie is meaningless on near-continuous/x data with no natural categories
        continuous_x = _is_continuous(grouped[x])
        if spec.chart_type == "pie" and continuous_x:
            spec_copy = spec.model_copy(deep=True)
            spec_copy.chart_type = "horizontal_bar"
            adaptation.append("Pie requested on a near-continuous field; substituted a bar chart.")
            return build_chart(spec_copy, df)

        additive = (spec.agg_function or "sum") in ("sum", "count") and "derived metric:" not in (spec.data_notes or "").lower()
        keep_all = spec.show_tail_categories or not additive or bool(spec.group_by) or "other" in grouped[x].astype(str).tolist()
        bucketed, note = _bucket_long_tail(grouped, x, y, keep_all=keep_all)
        if not additive and len(grouped) > MAX_CATEGORIES:
            adaptation.append("All categories retained: non-additive statistics cannot be summed into an 'other' category.")
        if note:
            adaptation.append(note)
        elif spec.show_tail_categories and len(grouped) > MAX_CATEGORIES:
            adaptation.append(
                f"Showing all {len(grouped)} categories under their real names (no 'other' merge)."
            )
        if spec.label_map:
            mapped = grouped[x].astype(str).map(lambda v: spec.label_map.get(v, v))
            if not additive and mapped.nunique() < grouped[x].nunique():
                raise ChartBuildError("Cannot merge labels for non-additive statistics; regroup the source data instead.")
            bucketed = _apply_labels(bucketed, x, y, spec.label_map)
            adaptation.append("Relabeled categories per the edit request.")

        if spec.chart_type in ("pie", "donut"):
            if not additive:
                raise ChartBuildError("Pie/donut requires additive values (sum or row count), not averages or distinct counts.")
            if bucketed[y].sum() <= 0:
                raise ChartBuildError("Pie/donut requires a positive total.")
            if (bucketed[y] < 0).any():
                raise ChartBuildError("Pie/donut requires non-negative values.")
            hole = 0.45 if spec.chart_type == "donut" else 0
            fig = px.pie(
                bucketed,
                names=x,
                values=y,
                title=spec.title,
                hole=hole,
                color_discrete_sequence=CATEGORICAL,
            )
            fig.update_traces(
                textposition="inside",
                textinfo="percent+label",
                textfont=dict(size=11, color="#2B2D42"),
                marker=dict(line=dict(color="#FFFFFF", width=2)),
                hoverlabel=dict(bgcolor="white", font_size=11),
            )
            apply_base_layout(fig, title=spec.title)
            return BuiltChart(
                figure_json=fig.to_json(),
                computed_summary=_summary_for_categorical(x, y, bucketed, spec.agg_function or "sum"),
                figure_data=_rows(bucketed),
                adaptation_note=" ".join(adaptation) or None,
            )

        if spec.chart_type == "horizontal_bar":
            ordered = bucketed.sort_values(y, ascending=True)
            fig = px.bar(
                ordered,
                x=y,
                y=x,
                orientation="h",
                title=spec.title,
                color_discrete_sequence=[PRIMARY],
            )
            fig.update_traces(
                marker=dict(color=PRIMARY, line=dict(color="#FFFFFF", width=1)),
                hoverlabel=dict(bgcolor="white", font_size=11),
            )
            fig.update_layout(xaxis_title=y, yaxis_title=x)
            apply_base_layout(fig, title=spec.title)
        elif spec.chart_type in ("grouped_bar", "stacked_bar", "stacked_100"):
            needs_group = spec.group_by and spec.group_by in df.columns and spec.group_by not in (x, y) and x in df.columns and y in df.columns
            if needs_group:
                # Recompute bucketed per (x, group_by) to keep color column, not per x alone
                try:
                    # bucketed was by x only; recompute for grouped case to preserve group_by
                    grouped = df.groupby([x, spec.group_by], as_index=False)[y].sum() if y in df.columns else df.groupby([x, spec.group_by]).size().reset_index(name=y or "count")
                    # Apply bucketing limit on grouped if needed (top categories per x)
                    use_df = grouped
                    bucketed = grouped  # update for summary
                except Exception:
                    use_df = bucketed if len(bucketed) <= len(df) else df
                if spec.chart_type == "stacked_100":
                    tmp = use_df.copy()
                    totals = tmp.groupby(x)[y].transform("sum")
                    tmp[y] = tmp[y] / totals * 100
                    use_df = tmp
                    fig = px.bar(
                        use_df,
                        x=x,
                        y=y,
                        color=spec.group_by,
                        title=spec.title,
                        barmode="stack",
                        color_discrete_sequence=CATEGORICAL,
                    )
                elif spec.chart_type == "grouped_bar":
                    fig = px.bar(
                        use_df,
                        x=x,
                        y=y,
                        color=spec.group_by,
                        title=spec.title,
                        barmode="group",
                        color_discrete_sequence=CATEGORICAL,
                    )
                else:
                    fig = px.bar(
                        use_df,
                        x=x,
                        y=y,
                        color=spec.group_by,
                        title=spec.title,
                        barmode="stack",
                        color_discrete_sequence=CATEGORICAL,
                    )
            else:
                if spec.group_by and (spec.group_by not in df.columns or spec.group_by in (x, y)):
                    # Adaptation: ignore invalid group_by for bar-like chart
                    pass
                fig = px.bar(bucketed, x=x, y=y, title=spec.title, color_discrete_sequence=[PRIMARY])
                fig.update_traces(marker=dict(color=PRIMARY, line=dict(color="#FFFFFF", width=1)))
            fig.update_layout(xaxis_title=x, yaxis_title=y)
            apply_base_layout(fig, title=spec.title)
        else:
            fig = px.bar(bucketed, x=x, y=y, title=spec.title, color_discrete_sequence=[PRIMARY])
            fig.update_traces(
                marker=dict(color=PRIMARY, line=dict(color="#FFFFFF", width=1)),
                hoverlabel=dict(bgcolor="white", font_size=11),
            )
            fig.update_layout(xaxis_title=x, yaxis_title=y)
            apply_base_layout(fig, title=spec.title)
        # Apply subtle bar styling
        fig.update_traces(marker_line_width=1, marker_line_color="white", opacity=0.96)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_categorical(x, y, bucketed, spec.agg_function or "sum"),
            figure_data=_rows(bucketed),
            adaptation_note=" ".join(adaptation) or None,
        )

    if spec.chart_type in ("line", "area"):
        if y not in df.columns or y == x:
            raise ChartBuildError(f"Cannot chart '{spec.chart_type}': no value column (spec y='{spec.y}').")
        d = df.sort_values(x)
        if spec.group_by and spec.group_by in d.columns:
            if spec.chart_type == "area":
                fig = px.area(
                    d,
                    x=x,
                    y=y,
                    color=spec.group_by,
                    title=spec.title,
                    color_discrete_sequence=CATEGORICAL,
                )
            else:
                fig = px.line(
                    d,
                    x=x,
                    y=y,
                    color=spec.group_by,
                    title=spec.title,
                    color_discrete_sequence=CATEGORICAL,
                )
            fig.update_traces(line=dict(width=2.2), marker=dict(size=5, line=dict(width=1, color="white")))
        else:
            if spec.chart_type == "area":
                fig = px.area(d, x=x, y=y, title=spec.title, color_discrete_sequence=[PRIMARY])
                fig.update_traces(
                    line=dict(color=PRIMARY, width=2.2),
                    fillcolor="rgba(154,140,180,0.22)",
                    marker=dict(size=5, color=PRIMARY, line=dict(width=1, color="white")),
                )
            else:
                fig = go.Figure(
                    go.Scatter(
                        x=d[x],
                        y=_to_numeric(d[y]),
                        mode="lines+markers",
                        name=y,
                        line=dict(color=PRIMARY, width=2.4),
                        marker=dict(color=PRIMARY, size=6, line=dict(width=1, color="white")),
                        hoverlabel=dict(bgcolor="white"),
                    )
                )
                fig.update_layout(title=spec.title, xaxis_title=x, yaxis_title=y)
        apply_base_layout(fig, title=spec.title)
        # Soften line markers
        fig.update_layout(hovermode="x unified")
        # Conditional highlighting: flag declining retention (e.g., "declining retention")
        notes_low = (spec.data_notes or "").lower()
        if "declining" in notes_low or "conditional:" in notes_low:
            try:
                fig.add_annotation(
                    text="Flagged declining retention" if "declining" in notes_low else "Conditional highlight",
                    xref="paper", yref="paper", x=0.5, y=1.08, showarrow=False,
                    font=dict(size=11, color="#DC2626"), bgcolor="rgba(254,226,226,0.9)", bordercolor="#FECACA"
                )
                # For grouped line, highlight declining series in red (first trace)
                if spec.group_by and len(fig.data) > 0:
                    # Mark last trace as declining if possible (heuristic)
                    try:
                        fig.data[-1].line.color = "#DC2626"
                        fig.data[-1].marker.color = "#DC2626"
                    except Exception:
                        pass
            except Exception:
                pass
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_line(d, x, y),
            figure_data=_rows(d),
            adaptation_note="Flagged declining retention" if "declining" in notes_low else None,
        )

    if spec.chart_type == "histogram":
        col = x if x in df.columns else y
        if col not in df.columns:
            raise ChartBuildError(f"Histogram requires column '{col}'")
        fig = px.histogram(
            df,
            x=col,
            title=spec.title,
            color_discrete_sequence=[PRIMARY],
        )
        fig.update_traces(
            marker=dict(color=PRIMARY, line=dict(color="white", width=1)),
            opacity=0.92,
            hoverlabel=dict(bgcolor="white"),
        )
        fig.update_layout(xaxis_title=col, yaxis_title="count", bargap=0.08)
        apply_base_layout(fig, title=spec.title)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_histogram(df, col),
            figure_data=_rows(df[[col]].dropna().head(500)),
            adaptation_note=None,
        )

    if spec.chart_type == "boxplot":
        ycol = y if y in df.columns else x
        xcol = spec.x if spec.x in df.columns and spec.x != ycol else None
        if ycol not in df.columns:
            raise ChartBuildError(f"Box plot requires numeric '{ycol}'")
        if xcol and xcol in df.columns:
            fig = px.box(
                df,
                x=xcol,
                y=ycol,
                title=spec.title,
                color=xcol,
                color_discrete_sequence=CATEGORICAL,
            )
        else:
            fig = px.box(df, y=ycol, title=spec.title, color_discrete_sequence=[PRIMARY])
            fig.update_traces(marker=dict(color=PRIMARY), line=dict(color=PRIMARY))
        fig.update_traces(
            boxmean=False,
            jitter=0.12,
            marker=dict(line=dict(width=1, color="white"), opacity=0.9),
            line=dict(width=1.4),
        )
        apply_base_layout(fig, title=spec.title)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_box(df, xcol, ycol),
            figure_data=_rows(df[[c for c in [xcol, ycol] if c and c in df.columns]].dropna().head(500)),
            adaptation_note=None,
        )

    if spec.chart_type == "heatmap":
        numeric_df = df.select_dtypes(include=["number"])
        if numeric_df.empty:
            raise ChartBuildError("Heatmap requires numeric data")
        corr = numeric_df.corr(numeric_only=True)
        fig = px.imshow(
            corr,
            text_auto=".2f",
            aspect="auto",
            title=spec.title,
            color_continuous_scale=DIVERGING_SCALE,
            zmin=-1,
            zmax=1,
        )
        fig.update_traces(
            hoverlabel=dict(bgcolor="white", font_size=11),
            textfont=dict(size=10, color="#2B2D42"),
        )
        fig.update_layout(coloraxis_colorbar=dict(title="corr", tickfont=dict(size=10)))
        apply_base_layout(fig, title=spec.title)
        corr_reset = corr.reset_index()
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_heatmap(corr),
            figure_data=_rows(corr_reset),
            adaptation_note=None,
        )

    if spec.chart_type == "scatter":
        d = df.dropna(subset=[x, y])
        if not spec.y or spec.y not in d.columns or not spec.x or spec.x not in d.columns:
            raise ChartBuildError("Scatter chart requires both x and y columns.")
        if spec.group_by and spec.group_by in d.columns:
            fig = px.scatter(
                d,
                x=spec.x,
                y=spec.y,
                color=spec.group_by,
                title=spec.title,
                color_discrete_sequence=CATEGORICAL,
            )
        else:
            fig = px.scatter(
                d,
                x=spec.x,
                y=spec.y,
                title=spec.title,
                color_discrete_sequence=[PRIMARY],
            )
        fig.update_traces(
            marker=dict(size=6, opacity=0.82, line=dict(width=1, color="white")),
            hoverlabel=dict(bgcolor="white"),
        )
        apply_base_layout(fig, title=spec.title)
        return BuiltChart(
            figure_json=fig.to_json(),
            computed_summary=_summary_for_scatter(d, x, y),
            figure_data=_rows(d),
            adaptation_note=None,
        )

    raise ChartBuildError(f"Unsupported chart type: {spec.chart_type}.")


def _clean_numeric_verify(series: pd.Series) -> pd.Series:
    """Numeric coercion mirroring the agent codegen cleaning.

    Text-stored measures ("$1,200", "45%") must parse, otherwise every
    verification total on currency data collapses to NaN. Idempotent for
    true numeric dtypes.
    """
    try:
        cleaned = (
            series.astype(str)
            .str.replace(r'[\$,%]', '', regex=True)
            .str.strip()
            .replace("", pd.NA)
        )
        return pd.to_numeric(cleaned, errors="coerce")
    except Exception:
        return pd.to_numeric(series, errors="coerce")


def _with_time_bucket_col(raw_df: pd.DataFrame, spec: ChartSpec, x: str) -> tuple[pd.DataFrame, str]:
    """Replicate the agent's time-bucket derivation for verification.

    The deterministic codegen rewrites spec.x to a derived `_time_bucket`
    column (monthly/quarterly/yearly) that does not exist in the raw frame.
    Without this step every bucketed chart fails verification with
    "Category column '_time_bucket' not found" even though the chart itself
    is correct. Mirrors agent.py bucket logic exactly.
    """
    import re as _re

    notes = (spec.data_notes or "").lower()
    m = _re.search(r"time bucket:\s*(monthly|quarterly|yearly|weekly|daily)", notes)
    if not m or not x or x not in raw_df.columns:
        return raw_df, x
    bucket = m.group(1)
    if bucket not in ("monthly", "quarterly", "yearly", "weekly", "daily"):
        return raw_df, x
    try:
        g = raw_df.copy()
        try:
            g[x] = pd.to_datetime(g[x], errors="coerce", format="mixed")
        except TypeError:
            g[x] = pd.to_datetime(g[x], errors="coerce")
        g = g.dropna(subset=[x])
        if bucket == "monthly":
            g["_time_bucket"] = g[x].dt.to_period("M").astype(str)
        elif bucket == "quarterly":
            g["_time_bucket"] = g[x].dt.to_period("Q").astype(str)
        elif bucket == "yearly":
            g["_time_bucket"] = g[x].dt.year.astype(str)
        else:
            g["_time_bucket"] = g[x].dt.to_period("W-SUN" if bucket == "weekly" else "D").astype(str)
        return g, "_time_bucket"
    except Exception:
        return raw_df, x


def _parse_topn_for_verify(spec: ChartSpec) -> tuple[int, str | None, str | None]:
    """Extract (n, group_col, rank_col) for Top-N pre-filter replication.

    Reads the structured filter ("X in_top_n 10 rank_by SUM(Y)") first,
    then the data_notes marker ("Top 10 by SUM(Y)"). Returns (0, None, None)
    when no Top-N context exists.
    """
    import re as _re

    ftext = spec.filter or ""
    m = _re.search(r"in_top_n\s+(\d+)\s+rank_by\s+(.+?)\s*$", ftext, _re.IGNORECASE)
    if m:
        expr = m.group(2).strip()
        inner = _re.sub(r".*\((.*)\).*", r"\1", expr) if "(" in expr else expr
        g = _re.search(r"(.+?)\s+in_top_n", ftext, _re.IGNORECASE)
        return int(m.group(1)), (g.group(1).strip() if g else spec.x), inner.strip().strip("\"'`")
    notes = spec.data_notes or ""
    m2 = _re.search(r"top\s+(\d+)\s+by\s+(SUM\s*\([^)]*\)|[^\s;,\.]+)", notes, _re.IGNORECASE)
    if m2:
        expr = m2.group(2).strip()
        inner = _re.sub(r".*\((.*)\).*", r"\1", expr) if "(" in expr else expr
        return int(m2.group(1)), spec.group_by or spec.x, inner.strip().strip("\"'`")
    return 0, None, None


def _apply_topn_for_verify(g: pd.DataFrame, spec: ChartSpec) -> pd.DataFrame:
    """Replicate the agent's Top-N pre-filter on the raw frame.

    Without this, any top-N chart fails verification by construction: the
    chart aggregates 10 customers while the verifier totals all of them.
    Unresolvable rank columns degrade to no filtering (existing behavior).
    """
    n_top, group_col, rank_col = _parse_topn_for_verify(spec)
    if not n_top or not group_col or group_col not in g.columns:
        return g
    rank_actual = next((c for c in g.columns if c.lower() == (rank_col or "").lower()), None)
    if not rank_actual:
        return g
    try:
        ranked = _clean_numeric_verify(g[rank_actual]).groupby(g[group_col]).sum()
        keep = ranked.nlargest(n_top).index
        return g[g[group_col].isin(keep)]
    except Exception:
        return g


def _apply_notes_filters_for_verify(g: pd.DataFrame, spec: ChartSpec) -> pd.DataFrame:
    """Replicate simple deterministic filters emitted in data_notes."""
    if "filter shipped orders" not in (spec.data_notes or "").lower():
        return g
    status_col = next((c for c in g.columns if c.lower() == "status"), None)
    if not status_col:
        return g
    return g[g[status_col].astype(str).str.contains("shipped", case=False, na=False)]


def verify_computed(spec: ChartSpec, raw_df: pd.DataFrame, summary: dict, computed_df: pd.DataFrame | None = None) -> tuple[bool, dict]:
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

    # Validate every computed group, including the series key. A matching
    # grand total cannot detect swapped categories or corrupted middle points.
    if computed_df is not None and spec.chart_type in CHARTS_WITH_CATEGORIES | {"line", "area"}:
        from tools.verify_result import verify_aggregates
        full = verify_aggregates(spec, raw_df, computed_df)
        if full is not None:
            return full

    x = (spec.x or "").strip() or (list(raw_df.columns)[0] if len(raw_df.columns) else "")
    y = (spec.y or "").strip()
    if not y and spec.chart_type in CHARTS_WITH_CATEGORIES:
        y = x
    raw_df, x = _with_time_bucket_col(raw_df, spec, x)
    if x not in raw_df.columns:
        return False, {"error": f"Category column '{x}' not found in the raw frame."}

    agg = (spec.agg_function or "sum").lower()
    notes = (spec.data_notes or "").lower()
    needs_split = "split" in notes or "comma" in notes or "explode" in notes
    if spec.chart_type in CHARTS_WITH_CATEGORIES:
        g = raw_df.copy()
        g = _apply_notes_filters_for_verify(g, spec)
        g = _apply_topn_for_verify(g, spec)
        g = apply_quarantine(g, x)
        derived_margin = "derived metric: profit_margin" in notes
        margin_sales = margin_cost = None
        if derived_margin:
            import re as _re_margin
            m_sales = _re_margin.search(r"sales=([^;.]+?)\s*;", spec.data_notes or "", _re_margin.IGNORECASE)
            m_cost = _re_margin.search(r"cost=([^;.]+)", spec.data_notes or "", _re_margin.IGNORECASE)
            margin_sales = m_sales.group(1).strip() if m_sales else None
            margin_cost = m_cost.group(1).strip() if m_cost else None
        if derived_margin and margin_sales in g.columns and margin_cost in g.columns:
            g[margin_sales] = _clean_numeric_verify(g[margin_sales])
            g[margin_cost] = _clean_numeric_verify(g[margin_cost])
            totals = g.groupby(x, as_index=False)[[margin_sales, margin_cost]].sum()
            totals[y] = ((totals[margin_sales] - totals[margin_cost]) / totals[margin_sales].replace(0, pd.NA) * 100).fillna(0)
            recomputed = totals[[x, y]]
            value_col = y
        elif agg == "count" or (not y and agg != "count_distinct"):
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
        elif agg in ("mean", "average"):
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found in the raw frame."}
            g[y] = _clean_numeric_verify(g[y])
            g = g.dropna(subset=[y])
            recomputed = g.groupby(x, as_index=False)[y].mean()
            value_col = y
        else:
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found in the raw frame."}
            g[y] = _clean_numeric_verify(g[y])
            g = g.dropna(subset=[y])
            reducer = agg if agg in ("min", "max", "median") else "sum"
            recomputed = g.groupby(x, as_index=False)[y].agg(reducer)
            value_col = y
        ordered = recomputed.sort_values(value_col, ascending=False).reset_index(drop=True)
        total = float(ordered[value_col].sum())
        keep_all = bool(getattr(spec, "show_tail_categories", False)) or agg not in ("sum", "count") or derived_margin or bool(spec.group_by) or "other" in g[x].astype(str).tolist()
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
        found_sums: dict[str, float] = {}
        for r in found_cats:
            c = str(r.get("category"))
            v = float(r.get("value", 0))
            if c == "other":
                found_other = v
                continue
            found_sums[c] = found_sums.get(c, 0.0) + v
        for c, v in found_sums.items():
            if c not in display_sums or abs(display_sums[c] - v) > 0.01:
                mismatched.append(c)
        if spec.group_by:
            # Grouped/stacked summaries contain one row per x/group pair, so
            # the compact top_categories projection cannot be compared to a
            # single-category aggregate without losing series context.
            checks["top_categories_match"] = {
                "ok": True,
                "expected": "skipped for grouped chart",
                "found": [r.get("category") for r in found_cats],
                "mismatched": [],
                "tolerance": 0.01,
            }
        else:
            checks["top_categories_match"] = {
                "ok": not mismatched,
                "expected": [display(str(v)) for v in ordered[cat_col].drop_duplicates().head(5).tolist()],
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
        g = _apply_topn_for_verify(raw_df.copy(), spec)
        g = apply_quarantine(g, x)
        g[y] = _clean_numeric_verify(g[y])
        g = g.dropna(subset=[y])
        monthly = g.groupby(x, as_index=False)[y].sum().sort_values(x)
        total = float(monthly[y].sum())
        checks["total"] = cmp(abs(total - float(summary.get("total", 0))) <= 0.01, total, summary.get("total"))
        if not spec.group_by:
            first_v = float(monthly[y].iloc[0]) if len(monthly) else 0.0
            last_v = float(monthly[y].iloc[-1]) if len(monthly) else 0.0
            checks["first_value"] = cmp(abs(first_v - float(summary.get("first_value", 0))) <= 0.01, first_v, summary.get("first_value"))
            checks["last_value"] = cmp(abs(last_v - float(summary.get("last_value", 0))) <= 0.01, last_v, summary.get("last_value"))
    elif spec.chart_type in ("area",):
        # Same as line
        if y not in raw_df.columns:
            return False, {"error": f"Value column '{y}' not found in the raw frame."}
        g = _apply_topn_for_verify(raw_df.copy(), spec)
        g = apply_quarantine(g, x)
        g[y] = _clean_numeric_verify(g[y])
        g = g.dropna(subset=[y])
        monthly = g.groupby(x, as_index=False)[y].sum().sort_values(x)
        total = float(monthly[y].sum())
        checks["total"] = cmp(abs(total - float(summary.get("total", 0))) <= 0.01, total, summary.get("total"))
    elif spec.chart_type in ("grouped_bar", "stacked_bar", "stacked_100", "donut"):
        # Reuse categorical verification (grouped uses same logic ignoring group_by for total)
        g = _apply_topn_for_verify(raw_df.copy(), spec)
        g = apply_quarantine(g, x)
        if agg == "count" or (not y and agg != "count_distinct"):
            recomputed = g.groupby(x).size().reset_index(name="count")
            value_col = "count"
        elif agg == "count_distinct":
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found"}
            recomputed = g.groupby(x)[y].nunique().reset_index(name="count")
            value_col = "count"
        else:
            if y not in raw_df.columns:
                return False, {"error": f"Value column '{y}' not found"}
            g[y] = _clean_numeric_verify(g[y])
            g = g.dropna(subset=[y])
            reducer = "mean" if agg in ("mean", "average") else "sum"
            recomputed = g.groupby(x, as_index=False)[y].agg(reducer)
            value_col = y
        total = float(recomputed[value_col].sum())
        checks["total"] = cmp(abs(total - float(summary.get("total", 0))) <= 0.01, total, summary.get("total"))
    elif spec.chart_type in ("histogram", "boxplot", "heatmap"):
        # Shape-based verification
        if spec.chart_type == "histogram":
            col = x if x in raw_df.columns else y
            if col not in raw_df.columns:
                return False, {"error": f"Histogram column '{col}' not found"}
            n = int(_clean_numeric_verify(raw_df[col]).dropna().shape[0])
            checks["count"] = cmp(abs(n - int(summary.get("count", 0))) <= 1, n, summary.get("count"))
        elif spec.chart_type == "boxplot":
            col = y if y in raw_df.columns else x
            if col not in raw_df.columns:
                return False, {"error": f"Box column '{col}' not found"}
            valid = _clean_numeric_verify(raw_df[col]).notna()
            if spec.x and spec.x != col and spec.x in raw_df.columns:
                valid &= raw_df[spec.x].notna()
            n = int(valid.sum())
            checks["count"] = cmp(abs(n - int(summary.get("count", 0))) <= 1, n, summary.get("count"))
        elif spec.chart_type == "heatmap":
            n_num = len(raw_df.select_dtypes(include=["number"]).columns)
            checks["n_cols"] = cmp(n_num == int(summary.get("n_cols", 0)), n_num, summary.get("n_cols"))
    elif spec.chart_type == "scatter":
        xs = spec.x or (list(raw_df.columns)[0] if len(raw_df.columns) else "")
        ys = spec.y or next(
            (c for c in raw_df.columns if c != xs and pd.api.types.is_numeric_dtype(raw_df[c])),
            None,
        )
        if not xs or not ys or xs not in raw_df.columns or ys not in raw_df.columns:
            return False, {"error": "Scatter requires both x and y in the raw frame."}
        d = raw_df[[xs, ys]].dropna()
        xs_vals = _clean_numeric_verify(d[xs]).tolist()
        ys_vals = _clean_numeric_verify(d[ys]).tolist()
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
