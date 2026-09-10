from __future__ import annotations

import pandas as pd

from agent import execute_plan, execute_spec
from ingestion import Workbook, profile_sheet
from planning import _ensure_valid_specs, plan_charts
from schemas import ChartSpec


def _workbook() -> Workbook:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=30),
        "region": ["North", "South", "East"] * 10,
        "customer": [f"C{i % 5}" for i in range(30)],
        "sales": range(30),
        "units": [1, 2, 3] * 10,
    })
    profile = profile_sheet("data", df, [])
    return Workbook(profiles=[profile], frames={"data": df}, raw=None)


def test_explicit_stacked_chart_is_not_replaced_by_line():
    wb = _workbook()
    specs = plan_charts(
        wb.profiles,
        ["Show sales by quarter and region as a stacked bar"],
        frames=wb.frames,
    )
    assert specs[0].chart_type == "stacked_bar"
    assert specs[0].group_by == "region"


def test_count_unique_resolves_entity_column():
    wb = _workbook()
    specs = plan_charts(wb.profiles, ["Count unique customers by month"], frames=wb.frames)
    assert specs[0].x == "date"
    assert specs[0].y == "customer"
    assert specs[0].agg_function == "count_distinct"
    assert specs[0].group_by is None
    result = execute_plan(wb, specs, attempt_llm=False)[0]
    assert result.figure_json and result.verified


def test_how_many_units_uses_sum():
    wb = _workbook()
    specs = plan_charts(wb.profiles, ["How many units by region"], frames=wb.frames)
    assert specs[0].y == "units"
    assert specs[0].agg_function == "sum"


def test_time_bucket_execution_does_not_mutate_spec():
    wb = _workbook()
    specs = plan_charts(wb.profiles, ["Monthly sales trend"], frames=wb.frames)
    assert specs[0].x == "date"
    first = execute_plan(wb, specs, attempt_llm=False)[0]
    second = execute_plan(wb, specs, attempt_llm=False)[0]
    assert first.figure_json and first.verified
    assert second.figure_json and second.verified
    assert specs[0].x == "date"


def test_invalid_group_by_is_explained_as_skipped():
    spec = ChartSpec(
        id="bad",
        sheet="data",
        chart_type="stacked_bar",
        title="Bad grouping",
        x="region",
        y="sales",
        group_by="missing_group",
    )
    result = _ensure_valid_specs([spec], _workbook().profiles)
    assert result[0].status == "skipped"
    assert "group_by" in (result[0].skip_reason or "")


def test_execution_failure_is_user_safe_and_categorized():
    spec = ChartSpec(
        id="bad_execution",
        sheet="data",
        chart_type="bar",
        title="Missing column",
        x="missing_dimension",
        y="sales",
        agg_function="sum",
    )
    result = execute_spec(spec, _workbook(), attempt_llm=False)
    assert result.figure_json is None
    assert result.execution_error_category == "missing_column"
    assert result.adaptation_note == "The chart calculation referenced a column that is not available in the dataset."
    assert "KeyError" in (result.execution_error or "")


def test_profit_margin_is_derived_from_sales_and_cost():
    df = pd.DataFrame({
        "Product": ["A", "A", "B", "B"],
        "Total_Sales_USD": [100, 200, 100, 100],
        "Cost_USD": [60, 100, 80, 50],
    })
    profile = profile_sheet("Sales", df, [])
    wb = Workbook(profiles=[profile], frames={"Sales": df}, raw=None)
    specs = plan_charts(wb.profiles, ["Show profit margin by product"], frames=wb.frames)
    assert specs[0].status == "planned"
    assert specs[0].y == "profit_margin"
    result = execute_plan(wb, specs, attempt_llm=False)[0]
    assert result.figure_json and result.verified
