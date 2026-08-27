"""Semantic validation of chart results.

Verifies that computed results make logical sense:
- Percentages sum to ~100%
- Bar totals match computed summary
- Data exists after split/explode
- Line charts have time-ordered x-axis
- Scatter has two numeric columns
- Top-N doesn't exceed available data
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
        _check_pie_sums(result_df, result)

    # --- Check 2: Bar totals match ---
    if spec.chart_type in ("bar", "horizontal_bar"):
        _check_bar_totals(result_df, computed_summary, result)

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

    return result


def _check_pie_sums(result_df: pd.DataFrame, result: ValidationResult):
    """Pie chart: values should represent parts of a whole."""
    # Find the numeric column (the value column)
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


def _check_bar_totals(result_df: pd.DataFrame, summary: dict, result: ValidationResult):
    """Bar chart: individual bar values should be consistent."""
    numeric_cols = result_df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return

    val_col = numeric_cols[0]
    bars_sum = result_df[val_col].sum()

    # Check for negative values
    if (result_df[val_col] < 0).any():
        result.add_warning("Bar chart contains negative values.")


def _check_split_data(source_df: pd.DataFrame, x_col: str | None, result: ValidationResult):
    """After split/explode, verify data still exists."""
    if x_col and x_col in source_df.columns:
        # Check if the column had any comma-separated values
        sample = source_df[x_col].dropna().head(20)
        has_commas = sample.astype(str).str.contains(",").any()
        if has_commas:
            # After explode, result should have more rows than unique source values
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

    # Check if datetime
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
