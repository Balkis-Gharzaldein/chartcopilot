"""Semantic validation of chart results.

Verifies that computed results make logical sense:
- Percentages sum to ~100%
- Bar totals match computed summary
- Data exists after split/explode
- Line charts have time-ordered x-axis
- Scatter has two numeric columns
- Top-N doesn't exceed available data
- Bar sum equals source total
- Count-distinct used for ID columns
"""

from __future__ import annotations

import pandas as pd

from schemas import ChartSpec


class ValidationResult:
    """Result of semantic validation."""
    def __init__(self):
        self.checks: dict[str, dict] = {}
        self.passed = True
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def add_check(self, name: str, passed: bool, message: str = ""):
        self.checks[name] = {"passed": passed, "message": message}
        if not passed:
            self.passed = False
            self.errors.append(f"{name}: {message}")

    def add_warning(self, message: str):
        self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def validate_chart(
    spec: ChartSpec,
    source_df: pd.DataFrame,
    result_df: pd.DataFrame | None,
    computed_summary: dict,
) -> ValidationResult:
    """Run semantic validation checks on a chart result.

    Args:
        spec: The chart specification.
        source_df: The original raw DataFrame (before aggregation).
        result_df: The aggregated result DataFrame used to build the chart.
        computed_summary: The numbers behind the chart.
    """
    result = ValidationResult()

    if result_df is None or result_df.empty:
        result.add_check("has_data", False, "No result data to validate.")
        return result

    result.add_check("has_data", True)

    # --- Check 1: Pie percentages sum to ~100% ---
    if spec.chart_type == "pie":
        _check_pie_sums(result_df, computed_summary, result)

    # --- Check 2: Bar totals match source data ---
    if spec.chart_type in ("bar", "horizontal_bar"):
        _check_bar_totals(result_df, source_df, spec, computed_summary, result)

    # --- Check 3: Data exists after split/explode ---
    notes = (spec.data_notes or "").lower()
    if "split" in notes or "explode" in notes:
        _check_split_data(source_df, spec.x, result)

    # --- Check 4: Line chart x-axis is time-ordered ---
    if spec.chart_type == "line":
        _check_line_ordering(result_df, spec.x, result)

    # --- Check 5: Scatter has two numeric columns ---
    if spec.chart_type == "scatter":
        _check_scatter_numeric(result_df, spec.x, spec.y, result)

    # --- Check 6: Top-N doesn't exceed available data ---
    if "top " in notes:
        _check_topn(result_df, notes, result)

    # --- Check 7: No null aggregation ---
    if "count" in (spec.agg_function or ""):
        _check_no_null_agg(result_df, result)

    # --- Check 8: Count-distinct for ID columns ---
    _check_id_count_distinct(spec, result)

    # --- Check 9: Percentages sum to ~100% (for any chart with percentages) ---
    _check_percentage_sum(result_df, computed_summary, result)

    return result


def _check_pie_sums(result_df: pd.DataFrame, computed_summary: dict, result: ValidationResult):
    """Pie chart: values should represent parts of a whole."""
    numeric_cols = result_df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        result.add_check("pie_has_values", False, "No numeric values found for pie chart.")
        return

    val_col = numeric_cols[0]
    total = result_df[val_col].sum()
    if total == 0:
        result.add_check("pie_nonzero_total", False, "Pie chart total is zero.")
        return

    result.add_check("pie_nonzero_total", True)

    # Check for negative values (unusual for pie)
    if (result_df[val_col] < 0).any():
        result.add_warning("Pie chart contains negative values — this is unusual for part-of-whole.")

    # Check if percentages sum to ~100% (if computed_summary has total)
    source_total = computed_summary.get("total")
    if source_total and source_total > 0:
        bars_sum = result_df[val_col].sum()
        pct_diff = abs(bars_sum - source_total) / source_total
        if pct_diff > 0.02:  # more than 2% off
            result.add_warning(
                f"Sum of pie slices ({bars_sum:.0f}) differs from source total "
                f"({source_total:.0f}) by {pct_diff:.1%}."
            )


def _check_bar_totals(
    result_df: pd.DataFrame,
    source_df: pd.DataFrame,
    spec: ChartSpec,
    summary: dict,
    result: ValidationResult,
):
    """Bar chart: bar sum should equal source total (for count/sum aggregations)."""
    numeric_cols = result_df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return

    val_col = numeric_cols[0]
    bars_sum = result_df[val_col].sum()

    # Check for negative values
    if (result_df[val_col] < 0).any():
        result.add_warning("Bar chart contains negative values.")

    # Verify sum matches source total for count aggregation
    agg = spec.agg_function or "sum"
    if agg == "count":
        source_total = len(source_df)
        if bars_sum != source_total:
            result.add_warning(
                f"Bar sum ({bars_sum:.0f}) does not match source row count "
                f"({source_total}). Some rows may have been excluded."
            )
    elif agg == "count_distinct":
        # For count_distinct, verify the total matches unique count
        source_total = summary.get("total")
        if source_total and abs(bars_sum - source_total) > 1:
            result.add_warning(
                f"Count-distinct sum ({bars_sum:.0f}) differs from expected "
                f"total ({source_total:.0f})."
            )


def _check_split_data(source_df: pd.DataFrame, x_col: str | None, result: ValidationResult):
    """After split/explode, verify data still exists."""
    if x_col and x_col in source_df.columns:
        sample = source_df[x_col].dropna().head(20)
        has_commas = sample.astype(str).str.contains(",").any()
        if has_commas:
            source_unique = source_df[x_col].nunique()
            if len(result) <= source_unique:
                result.add_warning(
                    "Split/explode may not have expanded comma-separated values as expected."
                )


def _check_line_ordering(result_df: pd.DataFrame, x_col: str | None, result: ValidationResult):
    """Line chart: x-axis should be time-ordered."""
    if not x_col or x_col not in result_df.columns:
        return

    x_series = result_df[x_col]

    if pd.api.types.is_datetime64_any_dtype(x_series):
        if not x_series.is_monotonic_increasing and not x_series.is_monotonic_decreasing:
            result.add_warning("Line chart x-axis is not sorted in time order.")


def _check_scatter_numeric(
    result_df: pd.DataFrame, x_col: str | None, y_col: str | None, result: ValidationResult
):
    """Scatter chart: both x and y should be numeric."""
    if x_col and x_col in result_df.columns:
        if not pd.api.types.is_numeric_dtype(result_df[x_col]):
            result.add_check(
                "scatter_x_numeric", False,
                f"Scatter x-axis column '{x_col}' is not numeric."
            )
        else:
            result.add_check("scatter_x_numeric", True)

    if y_col and y_col in result_df.columns:
        if not pd.api.types.is_numeric_dtype(result_df[y_col]):
            result.add_check(
                "scatter_y_numeric", False,
                f"Scatter y-axis column '{y_col}' is not numeric."
            )
        else:
            result.add_check("scatter_y_numeric", True)


def _check_topn(result_df: pd.DataFrame, notes: str, result: ValidationResult):
    """Top-N: requested N shouldn't exceed available data."""
    import re
    m = re.search(r"top\s+(\d+)", notes)
    if m:
        requested_n = int(m.group(1))
        if len(result_df) < requested_n:
            result.add_warning(
                f"Requested top {requested_n} but only {len(result_df)} categories available."
            )


def _check_no_null_agg(result_df: pd.DataFrame, result: ValidationResult):
    """Check that count/aggregation didn't produce nulls in the value column."""
    numeric_cols = result_df.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        if result_df[col].isna().any():
            result.add_warning(f"Aggregated column '{col}' contains null values.")


def _check_id_count_distinct(spec: ChartSpec, result: ValidationResult):
    """If agg=count on an ID column, warn that count_distinct may be more appropriate."""
    if spec.agg_function in ("count", None) and spec.y:
        name_lower = spec.y.lower().strip()
        id_names = {"id", "ids", "identifier", "key", "uuid", "url", "link", "code", "token"}
        is_id = (
            name_lower in id_names
            or "_id" in name_lower
            or name_lower.startswith("id")
            or name_lower.endswith("id")
        )
        if is_id:
            result.add_warning(
                f"Column '{spec.y}' appears to be an ID column. "
                "Consider using count_distinct instead of count for unique values."
            )


def _check_percentage_sum(
    result_df: pd.DataFrame, computed_summary: dict, result: ValidationResult
):
    """If the result appears to be percentages, verify they sum to ~100%."""
    numeric_cols = result_df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return

    val_col = numeric_cols[0]
    total = result_df[val_col].sum()

    # Check if values look like percentages (all between 0 and 100, sum near 100)
    if total > 0 and total <= 105 and (result_df[val_col] >= 0).all() and (result_df[val_col] <= 100).all():
        if abs(total - 100) > 2:
            result.add_warning(
                f"Values look like percentages but sum to {total:.1f}% instead of 100%."
            )

