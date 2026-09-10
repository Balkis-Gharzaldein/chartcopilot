"""Numerical regression tests: assert plotted data, not merely chart existence."""

import pandas as pd
import pytest

from agent import execute_spec
from ingestion import Workbook, profile_sheet
from schemas import ChartSpec
from tools.create_chart import build_chart, ChartBuildError
from tools.verify_result import verify_aggregates
from viz.candidates import generate_exploratory
from viz.profiler import profile_data
from viz.ranking import RankedCandidate, select_diverse
from viz.scoring import score_candidate


def workbook(df):
    return Workbook([profile_sheet("data", df, [])], {"data": df})


def spec(**kwargs):
    kwargs.setdefault("y", None)
    return ChartSpec(id="accuracy", sheet="data", title="Accuracy check", **kwargs)


@pytest.mark.parametrize("chart_type", ["bar", "line", "grouped_bar"])
def test_distinct_string_entities_are_not_coerced_to_nan(chart_type):
    df = pd.DataFrame({"Date": ["2024-01-01"] * 3 + ["2024-02-01"],
                       "Customer": ["001", "1", "Alice", "Alice"], "Region": ["A"] * 4})
    s = spec(chart_type=chart_type, x="Date", y="Customer", agg_function="count_distinct",
             group_by="Region" if chart_type == "grouped_bar" else None)
    result = execute_spec(s, workbook(df), attempt_llm=False)
    assert result.figure_json, result.adaptation_note
    assert result.verified, result.verification
    assert [r["count"] for r in result.figure_data] == [3, 1]
    assert df.Customer.tolist() == ["001", "1", "Alice", "Alice"]


@pytest.mark.parametrize("agg,expected", [("mean", [20, 50]), ("min", [10, 40]), ("max", [30, 60])])
@pytest.mark.parametrize("chart_type", ["line", "bar", "grouped_bar"])
def test_non_sum_aggregations_match_each_source_group(agg, expected, chart_type):
    df = pd.DataFrame({"Date": ["2024-01-01"] * 2 + ["2024-02-01"] * 2,
                       "Value": [10, 30, 40, 60], "Region": ["A"] * 4})
    s = spec(chart_type=chart_type, x="Date", y="Value", agg_function=agg,
             group_by="Region" if chart_type == "grouped_bar" else None)
    r = execute_spec(s, workbook(df), attempt_llm=False)
    assert r.figure_json, r.adaptation_note
    assert r.verified, r.verification
    rows = sorted(r.figure_data, key=lambda row: row["Date"])
    assert [row["Value"] for row in rows] == expected


def test_verification_detects_swapped_series_with_unchanged_total():
    df = pd.DataFrame({"Category": ["A", "A", "B", "B"], "Group": ["X", "Y", "X", "Y"], "Value": [10, 20, 30, 40]})
    s = spec(chart_type="grouped_bar", x="Category", y="Value", group_by="Group", agg_function="sum")
    wrong = df.copy()
    wrong["Value"] = [20, 10, 40, 30]
    assert not verify_aggregates(s, df, wrong)[0]
    assert verify_aggregates(s, df, df)[0]


def test_failed_full_verification_withholds_chart(monkeypatch):
    import agent
    df = pd.DataFrame({"Category": ["A", "B"], "Value": [10, 20]})
    monkeypatch.setattr(agent, "_codegen_deterministic", lambda *args: "result = df.copy(); result['Value'] = [20, 10]")
    r = execute_spec(spec(chart_type="bar", x="Category", y="Value", agg_function="sum"), workbook(df), attempt_llm=False)
    assert not r.figure_json
    assert r.execution_error_category == "verification_failed"


@pytest.mark.parametrize("bucket,counts", [("monthly", [3]), ("weekly", [2, 1]), ("daily", [2, 1])])
def test_bucketed_row_counts_include_rows_with_missing_measures(bucket, counts):
    df = pd.DataFrame({"Date": ["2024-01-01", "2024/01/01", "2024-01-08"], "Value": [1, None, None]})
    r = execute_spec(spec(chart_type="line", x="Date", agg_function="count", data_notes=f"Time bucket: {bucket}."), workbook(df), attempt_llm=False)
    assert r.figure_json, r.adaptation_note
    assert r.verified, r.verification
    assert [row["count"] for row in r.figure_data] == counts


def test_mean_long_tail_never_sums_averages_into_other():
    df = pd.DataFrame({"Category": [f"C{i}" for i in range(12)], "Value": list(range(12))})
    chart = build_chart(spec(chart_type="bar", x="Category", y="Value", agg_function="mean"), df)
    assert len(chart.figure_data) == 12
    assert all(r["Category"] != "other" for r in chart.figure_data)
    with pytest.raises(ChartBuildError, match="additive"):
        build_chart(spec(chart_type="pie", x="Category", y="Value", agg_function="mean"), df)


def test_profiler_roles_and_duplicate_timestamp_gaps():
    df = pd.DataFrame({"Candidate": ["a", "b", "a", "b", "a", "b"],
                       "Amount": ["$1,000", "$2,000", "$3,000", "$4,000", "$5,000", "$6,000"],
                       "Date": ["2024-01-01"] * 2 + ["2024-01-02"] * 2 + ["2024-01-05"] * 2})
    dp = profile_data(workbook(df).profiles[0], df)
    assert dp.by_name("Candidate").role == "categorical"
    assert dp.by_name("Amount").role == "numeric"
    assert dp.by_name("Amount").mean == 3500
    assert not dp.by_name("Amount").is_multi_valued
    assert dp.by_name("Date").temporal_min.startswith("2024-01-01")


def test_overview_is_stable_and_avoids_summing_prices_or_constants():
    df = pd.DataFrame({"Region": ["A", "B"] * 10, "Price": list(range(20)), "Revenue": list(range(20)), "Constant": [1] * 20})
    def candidates(frame):
        return generate_exploratory(profile_data(workbook(frame).profiles[0], frame), "data")
    a, b = candidates(df), candidates(df[df.columns[::-1]])
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]
    assert all("Constant" not in (s.x, s.y) for s in a)
    assert all(s.agg_function == "mean" for s in a if s.y == "Price")
    assert any(s.y == "Revenue" and s.agg_function == "sum" for s in a)
    for s in a:
        r = execute_spec(s, workbook(df), attempt_llm=False)
        assert r.figure_json, (s, r.adaptation_note)


def test_categorical_only_overview_and_no_redundant_padding():
    df = pd.DataFrame({"Region": ["A", "B"] * 5})
    dp = profile_data(workbook(df).profiles[0], df)
    candidates = generate_exploratory(dp, "data")
    assert candidates and all(s.agg_function == "count" for s in candidates)
    first = candidates[0]
    second = first.model_copy(update={"chart_type": "pie"})
    ranked = [RankedCandidate(s, 80, "comparison", "", {}, []) for s in (first, second)]
    assert len(select_diverse(ranked)) == 1
    assert select_diverse([RankedCandidate(first, 20, "comparison", "", {}, [])]) == []


def test_scores_distinguish_completeness_instead_of_saturating():
    df = pd.DataFrame({"Region": ["A", "B"] * 10, "Complete": list(range(20)), "Sparse": [None] * 10 + list(range(10))})
    dp = profile_data(workbook(df).profiles[0], df)
    a = score_candidate(spec(chart_type="bar", x="Region", y="Complete"), dp, "comparison")[0]
    b = score_candidate(spec(chart_type="bar", x="Region", y="Sparse"), dp, "comparison")[0]
    assert b < a < 100


@pytest.mark.parametrize("kind", ["sales", "categorical", "temporal"])
def test_csv_upload_to_overview_executes_verified_charts(kind):
    from ingestion import ingest_file
    from planning import plan_charts
    frames = {
        "sales": pd.DataFrame({"Region": ["North", "South"] * 15, "Revenue": list(range(30)), "Price": list(range(30, 60))}),
        "categorical": pd.DataFrame({"Region": ["North", "South"] * 15}),
        "temporal": pd.DataFrame({"Date": pd.date_range("2024-01-01", periods=30).astype(str)}),
    }
    wb = ingest_file(frames[kind].to_csv(index=False).encode(), "known.csv")
    specs = plan_charts(wb.profiles, ["Analyze this data"], frames=wb.frames)
    assert 1 <= len(specs) <= 5
    for s in specs:
        assert s.id.startswith("exp_"), s
        r = execute_spec(s, wb, attempt_llm=False)
        assert r.figure_json, (s, r.adaptation_note)
        assert r.verified, (s, r.verification)


def test_mean_narrative_never_claims_sum_of_means_is_a_total():
    from narrative import _deterministic_narrative
    df = pd.DataFrame({"Category": ["A", "A", "B"], "Price": [10, 30, 100]})
    r = execute_spec(spec(chart_type="bar", x="Category", y="Price", agg_function="mean"), workbook(df), attempt_llm=False)
    text = _deterministic_narrative([r])
    assert "120" not in text
    assert "100" in text and "mean" in text


@pytest.mark.parametrize("dataset", ["time_series", "missing", "rich", "mixed_dates"])
def test_overview_on_existing_messy_datasets_is_verified(dataset):
    from tests.test_quality_validation import DATASETS, _explore
    _, _, wb, _, planned, _ = _explore(DATASETS[dataset]())
    assert planned
    for s in planned:
        result = execute_spec(s, wb, attempt_llm=False)
        assert result.figure_json, (s.title, result.adaptation_note, result.verification)
        assert result.verified, (s.title, result.verification)


def test_top_series_retains_all_category_series_pairs():
    df = pd.DataFrame({"Category": ["A", "B"] * 3, "Group": ["X", "X", "Y", "Y", "Z", "Z"], "Value": [100, 200, 10, 20, 1, 2]})
    s = spec(chart_type="grouped_bar", x="Category", y="Value", group_by="Group", agg_function="sum", data_notes="Top 2 by SUM(Value).")
    r = execute_spec(s, workbook(df), attempt_llm=False)
    assert r.figure_json and r.verified, r.verification
    assert len(r.figure_data) == 4
    assert {row["Group"] for row in r.figure_data} == {"X", "Y"}
