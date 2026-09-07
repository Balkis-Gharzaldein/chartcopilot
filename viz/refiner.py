"""Chart refinement logic — parses natural language refinement requests
and applies them to ChartSpec / figure_json.

Reuses existing intent detection and chart generation without rewriting
visualization intelligence. Handles:
- color palette changes
- axis label / title changes
- range adjustments
- chart type swaps
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemas import ChartSpec, ChartResult
from ingestion import Workbook
from agent import execute_spec
import json
import plotly.io as pio

# Simple color name -> hex mapping for common colors
COLOR_MAP = {
    "blue": "#3B82F6",
    "orange": "#F59E0B",
    "red": "#EF4444",
    "green": "#10B981",
    "purple": "#8B5CF6",
    "teal": "#14B8A6",
    "yellow": "#EAB308",
    "pink": "#EC4899",
    "gray": "#6B7280",
    "grey": "#6B7280",
    "navy": "#1E3A8A",
    "lavender": "#A78BFA",
}

CHART_TYPE_KEYWORDS = {
    "bar": "bar",
    "horizontal bar": "horizontal_bar",
    "horizontal_bar": "horizontal_bar",
    "grouped bar": "grouped_bar",
    "stacked bar": "stacked_bar",
    "line": "line",
    "area": "area",
    "scatter": "scatter",
    "histogram": "histogram",
    "box": "boxplot",
    "boxplot": "boxplot",
    "heatmap": "heatmap",
    "pie": "pie",
    "donut": "donut",
}

@dataclass
class RefinementIntent:
    color_palette: list[str] | None = None
    chart_type: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    title: str | None = None
    y_range: tuple[float, float] | None = None
    x_range: tuple[float, float] | None = None
    x_tickformat: str | None = None
    raw: str = ""


def parse_refinement_intent(request: str) -> RefinementIntent:
    low = request.lower()
    intent = RefinementIntent(raw=request)

    # Chart type
    for kw, ctype in CHART_TYPE_KEYWORDS.items():
        if kw in low:
            # Ensure it's a chart type change request, not just mentioning
            if any(v in low for v in ["change", "make", "convert", "switch", "instead"]):
                intent.chart_type = ctype
                break
            # Also if explicitly "make it a line chart"
            if f"a {kw}" in low or f"an {kw}" in low or f"to {kw}" in low:
                intent.chart_type = ctype
                break
    # Fallback: if line chart mentioned without verb but clearly
    if not intent.chart_type:
        for kw, ctype in CHART_TYPE_KEYWORDS.items():
            if re.search(rf"\b{re.escape(kw)}\b.*chart", low) or re.search(rf"chart.*\b{re.escape(kw)}\b", low):
                intent.chart_type = ctype
                break

    # Colors: extract color names
    colors = []
    for name, hexv in COLOR_MAP.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            colors.append(hexv)
    if colors:
        intent.color_palette = colors

    # Title: "Add a title 'Revenue Trends'" or 'title "foo"'
    m = re.search(r"title\s*['\"]([^'\"]+)['\"]", request, re.IGNORECASE)
    if m:
        intent.title = m.group(1).strip()
    else:
        m2 = re.search(r"title\s+(.+)", request, re.IGNORECASE)
        if m2 and len(m2.group(1).strip()) < 50:
            # Heuristic: if after title there's quoted or short phrase
            cand = m2.group(1).strip().strip('"').strip("'")
            if cand and len(cand.split()) <= 5:
                intent.title = cand

    # Y-range: "Y-axis range from 0 to 1000" or "y range 0-1000"
    m = re.search(r"y[-\s]*axis.*range.*from\s*([-\d\.]+)\s*to\s*([-\d\.]+)", low)
    if not m:
        m = re.search(r"y[-\s]*range\s*([-\d\.]+)\s*[-to]+\s*([-\d\.]+)", low)
    if m:
        try:
            intent.y_range = (float(m.group(1)), float(m.group(2)))
        except Exception:
            pass

    # X-range
    m = re.search(r"x[-\s]*axis.*range.*from\s*([-\d\.]+)\s*to\s*([-\d\.]+)", low)
    if m:
        try:
            intent.x_range = (float(m.group(1)), float(m.group(2)))
        except Exception:
            pass

    # Axis label: "Show months instead of dates on X-axis" -> month format
    if "month" in low and "x-axis" in low:
        intent.x_tickformat = "month"
    if "x-axis label" in low or "x axis label" in low:
        m = re.search(r"x[-\s]*axis.*label.*['\"]([^'\"]+)['\"]", request, re.IGNORECASE)
        if m:
            intent.x_label = m.group(1)
    if "y-axis label" in low or "y axis label" in low:
        m = re.search(r"y[-\s]*axis.*label.*['\"]([^'\"]+)['\"]", request, re.IGNORECASE)
        if m:
            intent.y_label = m.group(1)

    return intent


def apply_refinement_to_spec(spec: ChartSpec, intent: RefinementIntent) -> tuple[ChartSpec, list[str]]:
    """Apply refinement intent to ChartSpec, return (new_spec, log_lines)."""
    new_spec = spec.model_copy(deep=True)
    logs: list[str] = []

    if intent.chart_type and intent.chart_type != spec.chart_type:
        new_spec.chart_type = intent.chart_type
        logs.append(f"Changed chart type to {intent.chart_type}")

    if intent.title:
        new_spec.title = intent.title
        logs.append(f"Updated title to '{intent.title}'")

    # Color palette is not stored in ChartSpec, will be applied to figure_json later
    if intent.color_palette:
        logs.append(f"Changed color palette to {', '.join(intent.color_palette)}")

    if intent.x_label:
        # Store as data_notes for now, but layout will be updated
        logs.append(f"Updated X-axis label to '{intent.x_label}'")

    if intent.y_label:
        logs.append(f"Updated Y-axis label to '{intent.y_label}'")

    if intent.y_range:
        logs.append(f"Set Y-axis range to {intent.y_range[0]}–{intent.y_range[1]}")

    if intent.x_tickformat == "month":
        logs.append("Formatted X-axis to show month names")

    if not logs:
        logs.append("Applied refinement request")

    return new_spec, logs


def apply_refinement_to_figure(figure_json: str, intent: RefinementIntent, spec: ChartSpec) -> str:
    """Apply visual refinements directly to Plotly figure_json."""
    try:
        fig = pio.from_json(figure_json)
    except Exception:
        return figure_json

    # Colors: update marker colors for each trace
    if intent.color_palette:
        palette = intent.color_palette
        for idx, trace in enumerate(fig.data):
            color = palette[idx % len(palette)]
            # For bar/pie/histogram marker, scatter marker, line color
            try:
                if hasattr(trace, 'marker') and trace.marker is not None:
                    trace.marker.color = color
                elif hasattr(trace, 'line') and trace.line is not None:
                    trace.line.color = color
                # For pie, marker colors is list
                if trace.type == 'pie':
                    # pie colors are per slice via marker.colors
                    n = len(trace.values) if hasattr(trace, 'values') and trace.values is not None else len(trace.labels) if hasattr(trace, 'labels') else 3
                    trace.marker.colors = [palette[i % len(palette)] for i in range(n)]
            except Exception:
                pass

    if intent.title:
        fig.layout.title.text = intent.title

    if intent.x_label and hasattr(fig.layout, 'xaxis') and fig.layout.xaxis:
        fig.layout.xaxis.title.text = intent.x_label
    if intent.y_label and hasattr(fig.layout, 'yaxis') and fig.layout.yaxis:
        fig.layout.yaxis.title.text = intent.y_label

    if intent.y_range and hasattr(fig.layout, 'yaxis') and fig.layout.yaxis:
        fig.layout.yaxis.range = list(intent.y_range)
        fig.layout.yaxis.autorange = False

    if intent.x_range and hasattr(fig.layout, 'xaxis') and fig.layout.xaxis:
        fig.layout.xaxis.range = list(intent.x_range)
        fig.layout.xaxis.autorange = False

    if intent.x_tickformat == "month":
        # Set xaxis tickformat to month
        if hasattr(fig.layout, 'xaxis') and fig.layout.xaxis:
            fig.layout.xaxis.tickformat = "%b %Y"
            fig.layout.xaxis.type = "date"

    return fig.to_json()


def refine_chart(workbook: Workbook, chart_index: int, results: list[ChartResult], refinement_request: str, current_spec: ChartSpec | None = None) -> tuple[ChartResult, str]:
    """Main entry: parse request, apply to spec, re-render, return new ChartResult and log."""
    if not (0 <= chart_index < len(results)):
        raise ValueError("Chart index out of range")
    
    target = results[chart_index]
    spec = current_spec or target.spec
    # Use provided spec if given, otherwise target's spec
    base_spec = spec if current_spec else target.spec

    intent = parse_refinement_intent(refinement_request)
    new_spec, spec_logs = apply_refinement_to_spec(base_spec, intent)

    # Metric/dimension change ("sum PCS instead", "group by Style"): resolve
    # against live columns and re-execute. Uses the same single resolver as
    # the agent chat path so both refine entries behave identically.
    try:
        from agent import _apply_column_change as _agent_col_change
        from viz.profiler import profile_data as _profile_data

        _prof = workbook.profile_for(base_spec.sheet)
        _dprof = _profile_data(_prof, workbook.frames.get(base_spec.sheet))
        _col_changed = _agent_col_change(
            new_spec, refinement_request,
            column_names=_dprof.column_names(),
            role_map={c.name: c.role for c in _dprof.columns},
        )
    except Exception:
        _col_changed = None
    if _col_changed is not None:
        new_spec, _col_note = _col_changed
        spec_logs.append(_col_note)
        from agent import execute_spec
        new_result = execute_spec(new_spec, workbook, attempt_llm=False)
        log = "\n".join(spec_logs)
        if new_result.figure_json:
            log += "\n✓ Chart re-rendered with new columns"
        return new_result, log

    # If chart type changed, re-execute via agent to regenerate data correctly
    if intent.chart_type and intent.chart_type != base_spec.chart_type:
        # Re-execute with new spec
        from agent import execute_spec
        new_result = execute_spec(new_spec, workbook, attempt_llm=False)
        # Apply visual refinements (like colors) to the newly generated figure
        if new_result.figure_json and intent.color_palette:
            new_result.figure_json = apply_refinement_to_figure(new_result.figure_json, intent, new_spec)
        log = "\n".join(spec_logs)
        if new_result.figure_json:
            log += "\n✓ Chart re-rendered with new type"
        return new_result, log

    # For non-type changes, just update the existing figure_json visually
    if target.figure_json:
        new_figure = apply_refinement_to_figure(target.figure_json, intent, new_spec)
        # Create new result with updated spec and figure
        new_result = target.model_copy(deep=True)
        new_result.spec = new_spec
        new_result.figure_json = new_figure
        # Keep other fields (figure_data, computed_summary) but update title if changed
        log = "\n".join(spec_logs)
        if intent.color_palette or intent.title or intent.y_range:
            log += "\n✓ Visuals updated"
        return new_result, log

    # Fallback: re-execute
    from agent import execute_spec
    new_result = execute_spec(new_spec, workbook, attempt_llm=False)
    return new_result, "\n".join(spec_logs)
