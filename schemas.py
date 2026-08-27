"""Pydantic v2 data models shared across ChartCopilot."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ChartType = Literal["line", "bar", "horizontal_bar", "pie", "scatter"]
PlotlyChartType = Literal[
    "line",
    "bar",
    "horizontal_bar",
    "pie",
    "scatter",
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
    chart_type: Literal["line", "bar", "horizontal_bar", "pie", "scatter"]
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

    @model_validator(mode="after")
    def _skip_needs_reason(self) -> "ChartSpec":
        if self.status == "skipped" and not self.skip_reason:
            self.skip_reason = "No reason provided."
        return self


class ChartResult(BaseModel):
    spec: ChartSpec
    figure_json: str | None = None  # plotly figure as JSON, None if skipped
    computed_summary: dict = Field(default_factory=dict)  # numbers behind the chart
    figure_data: list[dict] = Field(default_factory=list)  # exact rows the figure was built from
    adaptation_note: str | None = None  # e.g. "bucketed 400 categories into top 10 + other"
    verified: bool = False  # independent recomputation matched computed_summary
    verification: dict = Field(default_factory=dict)  # per-check results

    @property
    def skipped(self) -> bool:
        return self.spec.status == "skipped"


class GuidelineLines(BaseModel):
    lines: list[str]
    source: Literal["instructions_sheet", "text_area"]
    instructions_sheet: str | None = None  # sheet name, when source is instructions_sheet


# Container used for OpenAI structured-output (response_format needs a top-level model).
class ChartSpecList(BaseModel):
    specs: list[ChartSpec] = Field(default_factory=list)