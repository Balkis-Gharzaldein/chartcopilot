"""Rule-based chart type selection engine.

Rules extracted from data-to-viz.com + analytical reasoning — applied after
line grouping, before ChartSpec finalization. User-explicit requests always win.
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
    if any(tok in x_norm for tok in date_tokens):
        return True
    for col in profile.columns:
        if col.name == x_col:
            if "datetime" in col.dtype.lower() or "date" in col.dtype.lower():
                return True
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


def _is_high_cardinality_id(col_name: str, profile: SheetProfile) -> bool:
    """Check if a column is a high-cardinality identifier (ID, URL, etc.)."""
    name_lower = col_name.lower().strip()
    id_names = {"id", "ids", "identifier", "key", "uuid", "url", "link", "code", "token"}
    if name_lower in id_names or name_lower.endswith("_id") or name_lower.endswith(" id"):
        return True
    for col in profile.columns:
        if col.name == col_name:
            if col.unique_count > 0 and profile.row_count > 0:
                if col.unique_count / profile.row_count > 0.9:
                    return True
    return False


def _has_comma_separated_values(col_name: str, profile: SheetProfile) -> bool:
    """Check if a column contains comma-separated values in its samples."""
    for col in profile.columns:
        if col.name == col_name and col.sample_values:
            for sv in col.sample_values[:5]:
                if "," in str(sv):
                    return True
    return False


def _get_row_count(profile: SheetProfile) -> int:
    return profile.row_count


# --- analytical reasoning ----------------------------------------------------

class AnalyticalReasoning:
    """Column-aware reasoning about data characteristics."""

    def __init__(self, spec: ChartSpec, profile: SheetProfile):
        self.spec = spec
        self.profile = profile
        self.notes: list[str] = []
        self.suggested_agg: str | None = None
        self.suggested_data_notes: list[str] = []
        self.top_n_override: int | None = None

    def analyze(self) -> ChartSpec:
        """Run all analytical reasoning checks and modify spec in-place."""
        self._check_id_column()
        self._check_comma_separated()
        self._check_top_n_overflow()
        self._check_domain_rules()
        self._apply_suggestions()
        return self.spec

    def _check_id_column(self):
        """If x or y is an ID-like column, suggest count_distinct."""
        x, y = self.spec.x, self.spec.y
        if x and _is_high_cardinality_id(x, self.profile):
            self.suggested_agg = "count_distinct"
            self.notes.append(
                f"Column '{x}' appears to be a high-cardinality identifier — "
                "using count_distinct instead of raw count."
            )
        if y and _is_high_cardinality_id(y, self.profile):
            if not self.suggested_agg:
                self.suggested_agg = "count_distinct"
            self.notes.append(
                f"Column '{y}' appears to be a high-cardinality identifier — "
                "using count_distinct."
            )

    def _check_comma_separated(self):
        """If the x column has comma-separated values, suggest split."""
        x = self.spec.x
        if x and _has_comma_separated_values(x, self.profile):
            existing = " ".join(self.spec.data_notes or "").lower()
            if "split" not in existing and "comma" not in existing:
                self.suggested_data_notes.append(
                    "Split comma-separated values into separate rows before aggregation."
                )
                self.notes.append(
                    f"Column '{x}' contains comma-separated values — will split before counting."
                )

    def _check_top_n_overflow(self):
        """If top-N exceeds available data, auto-reduce N."""
        notes = (self.spec.data_notes or "").lower()
        m = re.search(r"top\s+(\d+)", notes)
        if m:
            requested_n = int(m.group(1))
            n_available = _count_categories(self.spec.x, self.profile)
            if n_available and requested_n > n_available:
                self.top_n_override = n_available
                self.notes.append(
                    f"Top {requested_n} requested but only {n_available} categories exist — "
                    f"reducing to top {n_available}."
                )

    def _check_domain_rules(self):
        """MECS-specific domain rules."""
        # Only apply when the user explicitly asks for a count AND the y column is an ID
        if self.spec.agg_function == "count" and self.spec.y:
            y_lower = self.spec.y.lower().strip()
            id_names = {"id", "ids", "identifier", "key", "uuid", "url", "link", "code", "token"}
            is_id = (
                y_lower in id_names
                or "_id" in y_lower
                or y_lower.startswith("id")
                or y_lower.endswith("id")
            )
            if is_id:
                self.suggested_agg = "count_distinct"
                self.notes.append(
                    f"MECS domain rule: '{self.spec.y}' is an ID column — using count_distinct."
                )

    def _apply_suggestions(self):
        """Apply accumulated suggestions to the spec."""
        if self.suggested_agg:
            self.spec.agg_function = self.suggested_agg

        if self.top_n_override is not None:
            # Replace the top-N in data_notes
            notes = self.spec.data_notes or ""
            notes = re.sub(r"top\s+\d+", f"top {self.top_n_override}", notes, flags=re.IGNORECASE)
            self.spec.data_notes = notes

        if self.suggested_data_notes:
            existing = self.spec.data_notes or ""
            self.spec.data_notes = (existing + " " + " ".join(self.suggested_data_notes)).strip()


# --- rule evaluation --------------------------------------------------------

class RuleResult:
    """Result of applying rules to a ChartSpec."""
    def __init__(self, spec: ChartSpec, notes: list[str], forced_type: str | None = None):
        self.spec = spec
        self.notes = notes
        self.forced_type = forced_type


def apply_rules(spec: ChartSpec, profile: SheetProfile) -> RuleResult:
    """Apply data-to-viz rules + analytical reasoning to refine chart spec.

    Rules:
    1. Long labels (>12 chars avg) → horizontal_bar (if currently bar)
    2. Many categories (>10) → horizontal_bar (if currently bar)
    3. Time column detected → line (if currently bar and user didn't specify)
    4. Pie with >6 categories → warning note
    5. Line with >5 group values → spaghetti warning

    Analytical Reasoning:
    6. ID column → count_distinct
    7. Comma-separated values → split/explode
    8. Top-N exceeds data → auto-reduce N
    9. MECS domain rules → count_distinct on ID
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

    # --- Rule 3: Time column → line ---
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

    # --- Apply the forced type ---
    if forced_type:
        spec.chart_type = forced_type

    # --- Append accumulated notes ---
    if notes:
        existing = spec.data_notes or ""
        spec.data_notes = (existing + " " + " ".join(notes)).strip()

    # --- Analytical Reasoning (column-aware) ---
    reasoning = AnalyticalReasoning(spec, profile)
    spec = reasoning.analyze()
    notes.extend(reasoning.notes)

    return RuleResult(spec=spec, notes=notes, forced_type=forced_type)
