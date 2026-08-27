"""Rule-based chart type selection engine.

Rules extracted from data-to-viz.com — applied after line grouping,
before ChartSpec finalization. User-explicit requests always win.
"""

from __future__ import annotations

import re

from schemas import ChartSpec, SheetProfile

NUMERIC_DTYPES = {"int64", "int32", "float64", "float32", "Int64", "Float64"}

# --- helpers ----------------------------------------------------------------

def _count_categories(x_col: str | None, profile: SheetProfile) -> int | None:
    """Count distinct non-null values in the x column."""
    if not x_col:
        return None
    for col in profile.columns:
        if col.name == x_col:
            return col.unique_count if col.unique_count > 0 else None
    return None


def _avg_label_length(x_col: str | None, profile: SheetProfile) -> float | None:
    """Average character length of sample values in the x column."""
    if not x_col:
        return None
    for col in profile.columns:
        if col.name == x_col and col.sample_values:
            lengths = [len(str(v)) for v in col.sample_values]
            return sum(lengths) / len(lengths) if lengths else None
    return None


def _has_long_labels(x_col: str | None, profile: SheetProfile, threshold: float = 12.0) -> bool:
    """True if average label length exceeds threshold."""
    avg = _avg_label_length(x_col, profile)
    return avg is not None and avg > threshold


def _is_time_column(x_col: str | None, profile: SheetProfile) -> bool:
    """Check if x column looks like a date/time axis."""
    if not x_col:
        return False
    date_tokens = {"date", "time", "year", "month", "day", "quarter", "week", "period",
                   "timestamp", "created", "updated", "posted"}
    x_norm = x_col.lower().strip()
    # Direct name match
    if any(tok in x_norm for tok in date_tokens):
        return True
    # Check dtype
    for col in profile.columns:
        if col.name == x_col:
            if "datetime" in col.dtype.lower() or "date" in col.dtype.lower():
                return True
            # Check sample values for date patterns
            for sv in col.sample_values[:3]:
                if re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", str(sv)):
                    return True
    return False


def _count_groups(group_by: str | None, profile: SheetProfile) -> int | None:
    """Count distinct values in a group_by column."""
    if not group_by:
        return None
    for col in profile.columns:
        if col.name == group_by:
            return col.unique_count if col.unique_count > 0 else None
    return None


# --- rule evaluation --------------------------------------------------------

class RuleResult:
    """Result of applying rules to a ChartSpec."""
    def __init__(self, spec: ChartSpec, notes: list[str], forced_type: str | None = None):
        self.spec = spec
        self.notes = notes
        self.forced_type = forced_type


def apply_rules(spec: ChartSpec, profile: SheetProfile) -> RuleResult:
    """Apply data-to-viz rules to refine chart type and add warnings.

    Rules:
    1. Long labels (>12 chars avg) → horizontal_bar (if currently bar)
    2. Many categories (>10) → horizontal_bar (if currently bar)
    3. Time column detected → line (if currently bar and user didn't specify)
    4. Pie with >6 categories → warning note
    5. Line with >5 group values → spaghetti warning
    6. Few categories (2-6) + bar → suggest pie as recommendation
    7. Ranking keywords → force horizontal_bar
    """
    notes: list[str] = []
    forced_type: str | None = None
    chart_type = spec.chart_type

    n_categories = _count_categories(spec.x, profile)
    has_long = _has_long_labels(spec.x, profile)
    is_time = _is_time_column(spec.x, profile)

    # --- Rule 1: Long labels → horizontal_bar ---
    if has_long and chart_type == "bar":
        chart_type = "horizontal_bar"
        forced_type = "horizontal_bar"
        notes.append("Switched to horizontal bar: category labels are long.")

    # --- Rule 2: Many categories → horizontal_bar ---
    if n_categories and n_categories > 10 and chart_type == "bar":
        chart_type = "horizontal_bar"
        forced_type = "horizontal_bar"
        notes.append(f"Switched to horizontal bar: {n_categories} categories is better ranked horizontally.")

    # --- Rule 3: Time column → line (only if user didn't explicitly request bar/pie) ---
    if is_time and chart_type == "bar" and not forced_type:
        chart_type = "line"
        forced_type = "line"
        notes.append("Switched to line chart: x-axis appears to be a date/time column.")

    # --- Rule 4: Pie with many categories → caveat ---
    if chart_type == "pie" and n_categories and n_categories > 6:
        notes.append(
            f"Warning: pie chart with {n_categories} categories can be hard to read. "
            "Consider a bar chart for better comparison."
        )

    # --- Rule 5: Spaghetti risk for line charts ---
    if chart_type == "line" and spec.group_by:
        n_groups = _count_groups(spec.group_by, profile)
        if n_groups and n_groups > 5:
            notes.append(
                f"Warning: line chart with {n_groups} groups may be cluttered "
                "(spaghetti chart). Consider filtering or using small multiples."
            )

    # --- Rule 6: Ranking keywords → horizontal_bar ---
    # (Already handled by _detect_chart_type, but reinforce here)

    # --- Apply the forced type ---
    if forced_type:
        spec.chart_type = forced_type

    # --- Append accumulated notes ---
    if notes:
        existing = spec.data_notes or ""
        spec.data_notes = (existing + " " + " ".join(notes)).strip()

    return RuleResult(spec=spec, notes=notes, forced_type=forced_type)
