"""Pydantic v2 data models shared across ChartCopilot."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ChartType = Literal[
    "line",
    "bar",
    "horizontal_bar",
    "pie",
    "scatter",
    "grouped_bar",
    "stacked_bar",
    "stacked_100",
    "area",
    "histogram",
    "boxplot",
    "heatmap",
    "donut",
]
PlotlyChartType = Literal[
    "line",
    "bar",
    "horizontal_bar",
    "pie",
    "scatter",
    "grouped_bar",
    "stacked_bar",
    "stacked_100",
    "area",
    "histogram",
    "boxplot",
    "heatmap",
    "donut",
]


class ColumnProfile(BaseModel):
    name: str
    dtype: str
    sample_values: list[str]
    null_count: int
    unique_count: int


class SheetProfile(BaseModel):
    sheet_name: str
    columns: list[ColumnProfile]
    row_count: int

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class ChartSpec(BaseModel):
    id: str
    sheet: str
    chart_type: ChartType
    title: str
    x: str | None
    y: str | None
    group_by: str | None = None
    filter: str | None = None  # human-readable filter description
    agg_function: str | None = None  # e.g. "sum", "mean", "count"
    label_map: dict[str, str] | None = None  # category -> display label
    show_tail_categories: bool = False  # show every category under its real name (no "other")
    data_notes: str | None = None  # special data handling (e.g. "split comma-separated values")
    status: Literal["planned", "skipped"] = "planned"
    skip_reason: str | None = None  # required if status == "skipped"
    confidence: float = 1.0
    uncertain: bool = False
    clarification: str | None = None

    @model_validator(mode="after")
    def _skip_needs_reason(self) -> "ChartSpec":
        if self.status == "skipped" and not self.skip_reason:
            self.skip_reason = "No reason provided."
        return self

    @model_validator(mode="after")
    def _group_by_coherence(self) -> "ChartSpec":
        # Normalize: group_by must not duplicate x/y and bar/pie/histogram should not have group_by
        if self.group_by and self.group_by in (self.x, self.y):
            object.__setattr__(self, "group_by", None)
        GROUP_AWARE = {"grouped_bar", "stacked_bar", "stacked_100", "line", "area", "scatter", "heatmap", "boxplot"}
        if self.chart_type in ("bar", "horizontal_bar", "pie", "donut", "histogram") and self.group_by is not None:
            object.__setattr__(self, "group_by", None)
        # Also ensure GROUP_AWARE types keep group_by only if needed
        if self.chart_type not in GROUP_AWARE and self.group_by is not None:
            # For safety, clear group_by for non-group-aware types
            if self.chart_type not in GROUP_AWARE:
                object.__setattr__(self, "group_by", None)
        return self


class ChartResult(BaseModel):
    spec: ChartSpec
    figure_json: str | None = None  # plotly figure as JSON, None if skipped
    computed_summary: dict = Field(default_factory=dict)  # numbers behind the chart
    figure_data: list[dict] = Field(default_factory=list)  # exact rows the figure was built from
    adaptation_note: str | None = None  # e.g. "bucketed 400 categories into top 10 + other"
    verified: bool = False  # independent recomputation matched computed_summary
    verification: dict = Field(default_factory=dict)  # per-check results
    recommendations: list["ChartResult"] = Field(default_factory=list)  # related chart suggestions
    validation: dict = Field(default_factory=dict)  # semantic validation results
    execution_error: str | None = None
    execution_error_category: str | None = None

    @property
    def skipped(self) -> bool:
        return self.spec.status == "skipped"


class GuidelineLines(BaseModel):
    lines: list[str]
    source: Literal["instructions_sheet", "text_area"]
    instructions_sheet: str | None = None  # sheet name, when source is instructions_sheet


# --- VizSpec: analyst-facing spec for any question form (7 keys, per user) ---

TimeBucket = Literal["daily", "weekly", "monthly", "quarterly", "yearly", "none"]


class VizMetric(BaseModel):
    expr: str = Field(description="Column name or derived expression, e.g. SUM(revenue), retention_rate, COUNT(deals)")
    agg: str | None = Field(default=None, description="Aggregation: sum, mean, count, count_distinct, percent, etc.")
    alias: str | None = Field(default=None)


class VizFilter(BaseModel):
    col: str = Field(description="Column to filter")
    op: str = Field(description="Operator: ==, !=, >, <, >=, <=, in, between, in_top_n, last_n")
    value: Any | None = Field(default=None, description="Value, list, or dict for complex filters")
    rank_by: str | None = Field(default=None, description="For in_top_n: metric to rank by, e.g. SUM(revenue)")
    time_range: str | None = Field(default=None, description="For temporal filters like last 2 years")


class VizConditionalHighlight(BaseModel):
    condition: str = Field(description="e.g. declining retention, outlier > mean+3std")
    field: str | None = Field(default=None)
    op: str | None = Field(default=None, description="e.g. slope<0, value>threshold")
    style: str | None = Field(default=None, description="e.g. red, annotate")
    annotate: bool = Field(default=True)


class VizExpectedChart(BaseModel):
    family: str = Field(description="Chart family: line, bar, pie, stacked, small multiples, etc.")
    chart_type: str | None = Field(default=None, description="Specific ChartType if known")
    multi: bool = Field(default=False, description="Multiple series/lines")
    small_multiples: bool = Field(default=False)
    stacked: bool = Field(default=False)
    annotations: list[str] | None = Field(default=None)


class VizSpec(BaseModel):
    intent: list[str] = Field(description="Analytical intents: comparison, trend, distribution, etc. List for multi-metric+ranking+filtering")
    dimensions: list[str] = Field(default_factory=list, description="Dimension columns: region, quarter, sales_channel, etc.")
    metrics: list[VizMetric] = Field(default_factory=list, description="Metrics: SUM(revenue), COUNT(deals), retention_rate, etc.")
    filters: list[VizFilter] = Field(default_factory=list, description="Structured filters, e.g. Top 5 regions, last 2 years, Q1 vs Q4")
    conditional_highlighting: VizConditionalHighlight | None = Field(default=None)
    expected_chart: VizExpectedChart | None = Field(default=None)
    time_bucket: TimeBucket = Field(default="none", description="Time aggregation: monthly, quarterly, yearly, etc.")
    title: str | None = Field(default=None, description="Human title for the VizSpec")
    confidence: float | None = Field(default=None)

    @model_validator(mode="after")
    def _check_metrics(self) -> "VizSpec":
        if len(self.metrics) > 2:
            raise ValueError(f"Too many metrics ({len(self.metrics)}): up to 2 charts per request, reject if >2 (per user Q2)")
        return self


from typing import Any

# Container used for OpenAI structured-output (response_format needs a top-level model).
class ChartSpecList(BaseModel):
    specs: list[ChartSpec] = Field(default_factory=list)


class VizSpecList(BaseModel):
    vizspecs: list[VizSpec] = Field(default_factory=list)
