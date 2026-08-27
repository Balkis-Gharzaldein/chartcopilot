"""Tests for rule engine, semantic validation, and recommendation engine."""

from __future__ import annotations

import pytest
from schemas import ChartSpec, SheetProfile, ColumnProfile
from tools.rule_engine import apply_rules
from tools.semantic_validation import validate_chart
from planning import recommend_charts
import pandas as pd


# --- fixtures ----------------------------------------------------------------

def _make_profile(
    sheet_name: str = "data",
    columns: list[dict] | None = None,
    row_count: int = 100,
) -> SheetProfile:
    """Helper to build a SheetProfile for testing."""
    if columns is None:
        columns = [
            {"name": "Category", "dtype": "object", "unique": 5, "samples": ["A", "B", "C"]},
            {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
        ]
    cols = []
    for c in columns:
        cols.append(ColumnProfile(
            name=c["name"],
            dtype=c["dtype"],
            sample_values=c.get("samples", []),
            null_count=0,
            unique_count=c.get("unique", 10),
        ))
    return SheetProfile(sheet_name=sheet_name, columns=cols, row_count=row_count)


def _make_spec(**kwargs) -> ChartSpec:
    """Helper to build a ChartSpec for testing."""
    defaults = {
        "id": "test_spec",
        "sheet": "data",
        "chart_type": "bar",
        "title": "Test Chart",
        "x": "Category",
        "y": "Value",
    }
    defaults.update(kwargs)
    return ChartSpec(**defaults)


# --- rule engine tests -------------------------------------------------------

class TestRuleEngine:
    def test_long_labels_switches_to_horizontal_bar(self):
        """Bar chart with long labels should switch to horizontal_bar."""
        profile = _make_profile(columns=[
            {"name": "Very Long Category Name Here", "dtype": "object", "unique": 5,
             "samples": ["This is a very long label", "Another very long label", "Third very long label"]},
            {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
        ])
        spec = _make_spec(x="Very Long Category Name Here")
        result = apply_rules(spec, profile)
        assert result.spec.chart_type == "horizontal_bar"
        assert any("long" in n.lower() for n in result.notes)

    def test_many_categories_switches_to_horizontal_bar(self):
        """Bar chart with >10 categories should switch to horizontal_bar."""
        profile = _make_profile(
            row_count=200,
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 15, "samples": ["c1", "c2", "c3"]},
                {"name": "Value", "dtype": "float64", "unique": 100, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat")
        result = apply_rules(spec, profile)
        assert result.spec.chart_type == "horizontal_bar"

    def test_pie_with_many_categories_gets_warning(self):
        """Pie chart with >6 categories should get a warning."""
        profile = _make_profile(
            row_count=100,
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 10, "samples": ["c1", "c2", "c3"]},
                {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat", chart_type="pie")
        result = apply_rules(spec, profile)
        assert any("hard to read" in n.lower() for n in result.notes)

    def test_bar_with_few_categories_unchanged(self):
        """Bar chart with 2-8 categories should stay as bar."""
        profile = _make_profile(
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 5, "samples": ["A", "B", "C"]},
                {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat")
        result = apply_rules(spec, profile)
        assert result.spec.chart_type == "bar"

    def test_line_chart_not_changed(self):
        """Line chart should not be changed by rules."""
        profile = _make_profile()
        spec = _make_spec(chart_type="line")
        result = apply_rules(spec, profile)
        assert result.spec.chart_type == "line"

    def test_scatter_not_changed(self):
        """Scatter chart should not be changed by rules."""
        profile = _make_profile()
        spec = _make_spec(chart_type="scatter")
        result = apply_rules(spec, profile)
        assert result.spec.chart_type == "scatter"


# --- semantic validation tests -----------------------------------------------

class TestSemanticValidation:
    def test_has_data_check(self):
        """Validation should fail if no data."""
        spec = _make_spec()
        df = pd.DataFrame({"Category": ["A", "B"], "Value": [1, 2]})
        result = validate_chart(spec, df, None, {})
        assert not result.passed
        assert "has_data" in result.checks

    def test_pie_nonzero_total(self):
        """Pie chart should check for zero total."""
        spec = _make_spec(chart_type="pie")
        df = pd.DataFrame({"Category": ["A", "B"], "Value": [0, 0]})
        result_df = pd.DataFrame({"Category": ["A", "B"], "Value": [0, 0]})
        result = validate_chart(spec, df, result_df, {})
        assert not result.checks.get("pie_nonzero_total", {}).get("passed", True)

    def test_scatter_numeric_check(self):
        """Scatter should check both axes are numeric."""
        spec = _make_spec(chart_type="scatter", x="Category", y="Value")
        df = pd.DataFrame({"Category": ["A", "B"], "Value": [1, 2]})
        result_df = pd.DataFrame({"Category": ["A", "B"], "Value": [1, 2]})
        result = validate_chart(spec, df, result_df, {})
        # Category is not numeric
        assert not result.checks.get("scatter_x_numeric", {}).get("passed", True)

    def test_bar_with_negative_values(self):
        """Bar chart with negative values should warn."""
        spec = _make_spec()
        df = pd.DataFrame({"Category": ["A", "B"], "Value": [1, -2]})
        result_df = pd.DataFrame({"Category": ["A", "B"], "Value": [1, -2]})
        result = validate_chart(spec, df, result_df, {})
        assert any("negative" in w.lower() for w in result.warnings)


# --- recommendation engine tests ---------------------------------------------

class TestRecommendationEngine:
    def test_bar_with_few_categories_recommends_pie(self):
        """Bar with 2-8 categories should recommend pie."""
        profile = _make_profile(
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 5, "samples": ["A", "B", "C"]},
                {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat", chart_type="bar")
        recs = recommend_charts(spec, profile)
        assert len(recs) >= 2
        assert recs[0].chart_type == "bar"  # original
        assert recs[1].chart_type == "pie"  # recommendation

    def test_bar_with_many_categories_recommends_horizontal_bar(self):
        """Bar with >10 categories should recommend horizontal_bar."""
        profile = _make_profile(
            row_count=200,
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 15, "samples": ["c1", "c2", "c3"]},
                {"name": "Value", "dtype": "float64", "unique": 100, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat", chart_type="bar")
        recs = recommend_charts(spec, profile)
        assert len(recs) >= 2
        assert recs[0].chart_type == "bar"
        assert recs[1].chart_type == "horizontal_bar"

    def test_pie_recommends_bar(self):
        """Pie should recommend bar for better comparison."""
        profile = _make_profile()
        spec = _make_spec(chart_type="pie")
        recs = recommend_charts(spec, profile)
        assert len(recs) >= 2
        assert recs[0].chart_type == "pie"
        assert recs[1].chart_type == "bar"

    def test_line_no_recommendations(self):
        """Line chart should not generate recommendations."""
        profile = _make_profile()
        spec = _make_spec(chart_type="line")
        recs = recommend_charts(spec, profile)
        assert len(recs) == 1  # only the original

    def test_scatter_no_recommendations(self):
        """Scatter chart should not generate recommendations."""
        profile = _make_profile()
        spec = _make_spec(chart_type="scatter")
        recs = recommend_charts(spec, profile)
        assert len(recs) == 1

    def test_recommendations_have_unique_ids(self):
        """All recommendation IDs should be unique."""
        profile = _make_profile(
            columns=[
                {"name": "Cat", "dtype": "object", "unique": 5, "samples": ["A", "B", "C"]},
                {"name": "Value", "dtype": "float64", "unique": 50, "samples": ["1.0", "2.0", "3.0"]},
            ],
        )
        spec = _make_spec(x="Cat", chart_type="bar")
        recs = recommend_charts(spec, profile)
        ids = [r.id for r in recs]
        assert len(ids) == len(set(ids))
