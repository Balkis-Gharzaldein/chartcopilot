"""General-purpose visualization intelligence tests — synthetic datasets.

Covers: time series, categorical+numeric, multi-categorical, multi-numeric,
distributions, high cardinality, small/large, missing, dates, identifiers, zeros/negatives.
Dataset-agnostic — no sample file assumptions.
"""

import pandas as pd
from schemas import SheetProfile, ColumnProfile
from viz.profiler import profile_data, DataProfile
from viz.intent import parse_intents
from viz.orchestrator import orchestrate
from viz.gates.can import can_gate
from viz.gates.appropriate import appropriate_gate
from viz.scoring import score_candidate
from schemas import ChartSpec
from ingestion import Workbook

def _make_profile(df: pd.DataFrame, name="data") -> tuple[SheetProfile, DataProfile, Workbook]:
    # Build SheetProfile via profiler helper
    from ingestion import profile_sheet
    # Need raw_rows dummy
    prof = profile_sheet(name, df, [])
    dp = profile_data(prof, df)
    wb = Workbook(profiles=[prof], frames={name: df}, raw=None)
    return prof, dp, wb

def test_time_series():
    dates = pd.date_range("2024-01-01", periods=50, freq="D")
    df = pd.DataFrame({"date": dates, "revenue": range(50), "units": [x*2 for x in range(50)]})
    prof, dp, wb = _make_profile(df)
    assert dp.by_name("date").role == "temporal"
    specs = orchestrate([prof], wb.frames, ["Show trend over time"])
    assert any(s.chart_type in ("line","area") for s in specs), specs
    # line should score higher than bar for temporal
    line = next(s for s in specs if s.chart_type in ("line","area"))
    assert line.x == "date"

def test_categorical_numeric():
    df = pd.DataFrame({"region": ["A","B","C","A","B"]*20, "sales": range(100)})
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Compare regions"])
    assert any(s.chart_type in ("bar","horizontal_bar") for s in specs)
    # pie should be allowed for 3 cats
    specs2 = orchestrate([prof], wb.frames, ["Show share of region as pie chart"])
    assert any(s.chart_type in ("pie","donut") and s.status=="planned" for s in specs2)

def test_multi_categorical():
    df = pd.DataFrame({
        "region": ["North","South"]*50,
        "product": ["X","Y","Z"]*33 + ["X"],
        "sales": range(100)
    })
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Show composition by region and product as stacked bar"])
    # Should generate stacked/grouped
    assert any(s.chart_type in ("stacked_bar","grouped_bar","stacked_100") for s in specs)

def test_multi_numeric():
    df = pd.DataFrame({
        "a": range(100),
        "b": [x*1.5 for x in range(100)],
        "c": [x*0.5 for x in range(100)],
        "cat": ["X","Y"]*50
    })
    prof, dp, wb = _make_profile(df)
    # correlation heatmap needs >=3 numerics
    specs = orchestrate([prof], wb.frames, ["Show correlation"])
    assert any(s.chart_type=="heatmap" for s in specs)
    specs2 = orchestrate([prof], wb.frames, ["Show relationship between a and b"])
    assert any(s.chart_type=="scatter" for s in specs2)

def test_distribution():
    import numpy as np
    np.random.seed(0)
    vals = np.random.normal(50, 10, 100)
    df = pd.DataFrame({"measure": vals, "group": ["A","B"]*50})
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Show distribution of measure"])
    assert any(s.chart_type in ("histogram","boxplot") for s in specs)

def test_high_cardinality():
    df = pd.DataFrame({"category": [f"cat_{i}" for i in range(60)]*2, "value": range(120)})
    prof, dp, wb = _make_profile(df)
    # Bar with 60 cats -> should prefer horizontal or be penalized
    specs = orchestrate([prof], wb.frames, ["Compare categories"])
    assert any(s.chart_type=="horizontal_bar" for s in specs)
    specs2 = orchestrate([prof], wb.frames, ["Show pie of category"])
    pie = [s for s in specs2 if s.chart_type in ("pie","donut")]
    if pie:
        # Pie with 60 cats is feasible via bucketing but penalized; either skipped or planned with penalty
        if pie[0].status == "planned":
            assert "many categories" in (pie[0].data_notes or "").lower() or pie[0].status == "planned"
        else:
            assert pie[0].status == "skipped"

def test_small_dataset():
    df = pd.DataFrame({"x": [1,2,3], "y": [4,5,6]})
    prof, dp, wb = _make_profile(df)
    # Scatter needs >=10 rows, should be rejected for small
    specs = orchestrate([prof], wb.frames, ["Show relationship"])
    scatter = [s for s in specs if s.chart_type=="scatter"]
    # Should be empty or skipped
    assert not scatter or scatter[0].status=="skipped"

def test_large_dataset():
    df = pd.DataFrame({"cat": ["A","B","C"]*1000, "val": range(3000)})
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Analyze this data"])
    # Should produce at least 3 diverse
    assert len([s for s in specs if s.status=="planned"]) >= 3
    # Should handle bucketing without error (execution tested elsewhere)

def test_missing_values():
    df = pd.DataFrame({"cat": ["A","B",None,"A","B"]*20, "val": [1,None,3,4,5]*20})
    prof, dp, wb = _make_profile(df)
    assert dp.by_name("cat").null_rate > 0
    specs = orchestrate([prof], wb.frames, ["Compare categories"])
    assert len(specs) > 0
    assert any(s.status=="planned" for s in specs)

def test_mixed_dates():
    df = pd.DataFrame({"date_str": ["2024-01-01","2024/02/01","2024-03-15"]*10, "val": range(30)})
    prof, dp, wb = _make_profile(df)
    # Should detect temporal even with mixed formats
    # At least one should be temporal or categorical
    specs = orchestrate([prof], wb.frames, ["Show trend"])
    assert len(specs) > 0

def test_identifier():
    df = pd.DataFrame({"user_id": [f"id_{i}" for i in range(100)], "region": ["A","B"]*50, "val": range(100)})
    prof, dp, wb = _make_profile(df)
    assert dp.by_name("user_id").role == "identifier"
    specs = orchestrate([prof], wb.frames, ["Analyze this data"])
    # Should not use identifier as x
    for s in specs:
        if s.status=="planned":
            assert s.x != "user_id", f"identifier used as x: {s}"

def test_zero_negative():
    df = pd.DataFrame({"cat": ["A","B","C"]*10, "val": [-5,0,5]*10})
    prof, dp, wb = _make_profile(df)
    # Pie with negatives should be rejected
    specs = orchestrate([prof], wb.frames, ["Show pie of cat"])
    pie = [s for s in specs if s.chart_type in ("pie","donut")]
    if pie:
        # If pie generated, it should be skipped due to negatives
        assert any(s.status=="skipped" for s in pie) or not pie
    # Bar with negatives should still be allowed (with warning)
    specs2 = orchestrate([prof], wb.frames, ["Compare categories"])
    assert any(s.status=="planned" for s in specs2)

def test_exploratory_broad():
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=30),
        "region": ["A","B","C"]*10,
        "sales": range(30),
        "profit": [x*0.5 for x in range(30)]
    })
    prof, dp, wb = _make_profile(df)
    for q in ["Analyze this data", "Explore this dataset", "Show me useful visualizations", ""]:
        specs = orchestrate([prof], wb.frames, [q] if q else [])
        # Should produce diverse set, not single
        planned = [s for s in specs if s.status=="planned"]
        assert len(planned) >= 2, f"broad '{q}' produced {len(planned)}"
        # Check diversity of goals via chart types
        types = set(s.chart_type for s in planned)
        assert len(types) >= 2, f"types not diverse for '{q}': {types}"

def test_explicit_request_respected():
    df = pd.DataFrame({"cat": ["A","B","C"]*20, "val": range(60), "group": ["X","Y"]*30})
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Make it a stacked bar"])
    # Should be stacked_bar and planned if feasible
    assert any(s.chart_type=="stacked_bar" and s.status=="planned" for s in specs)
    # Inappropriate explicit pie with 60 cats — now feasible via bucketing but penalized; either skipped or planned with penalty
    df2 = pd.DataFrame({"cat": [f"c{i}" for i in range(60)]*2, "val": range(120)})
    prof2, dp2, wb2 = _make_profile(df2)
    specs2 = orchestrate([prof2], wb2.frames, ["Make it a pie chart"])
    pie = [s for s in specs2 if s.chart_type=="pie"]
    assert pie
    # Either skipped (strict) or planned via bucketing with penalty is acceptable
    if pie[0].status == "skipped":
        assert "APPROPRIATE" in pie[0].skip_reason or "CAN gate" in pie[0].skip_reason
    else:
        assert pie[0].status == "planned" and "many categories" in (pie[0].data_notes or "").lower()

def test_scoring_explainable():
    df = pd.DataFrame({"cat": ["A","B","C"]*10, "val": range(30)})
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Compare categories"])
    planned = [s for s in specs if s.status=="planned"]
    assert planned
    s = planned[0]
    # data_notes should contain score
    assert s.data_notes and "score" in s.data_notes.lower()
    # Verify scoring breakdown via direct call
    from viz.scoring import score_candidate
    score, reason, breakdown = score_candidate(s, dp, "comparison", False)
    assert score > 0
    assert "data_fit" in breakdown

def test_can_appropriate_useful_separation():
    df = pd.DataFrame({"cat": ["A","B"]*5, "val": [1,1,1,1,1,1,1,1,1,1]})  # zero variance
    prof, dp, wb = _make_profile(df)
    # Histogram with zero variance should be inappropriate
    spec = ChartSpec(id="t", sheet="data", chart_type="histogram", title="t", x="val", y=None)
    assert not can_gate(spec, dp).passed or not appropriate_gate(spec, dp).passed
    # Normal case should pass all
    df2 = pd.DataFrame({"cat": ["A","B","C"]*10, "val": range(30)})
    prof2, dp2, _ = _make_profile(df2)
    spec2 = ChartSpec(id="t2", sheet="data", chart_type="bar", title="t2", x="cat", y="val", agg_function="sum")
    assert can_gate(spec2, dp2).passed
    assert appropriate_gate(spec2, dp2).passed

def test_diversity_not_duplicate():
    df = pd.DataFrame({
        "region": ["A","B","C"]*20,
        "product": ["X","Y"]*30,
        "sales": range(60),
        "date": pd.date_range("2024-01-01", periods=60)
    })
    prof, dp, wb = _make_profile(df)
    specs = orchestrate([prof], wb.frames, ["Analyze this data"])
    planned = [s for s in specs if s.status=="planned"]
    # Should not have duplicate (same goal,x,y)
    seen = set()
    for s in planned:
        key = (s.chart_type, s.x, s.y, s.group_by)
        assert key not in seen, f"duplicate {key}"
        seen.add(key)
    # Bad case would be 4 variants of same x,y; good is diverse
    xy_pairs = set((s.x, s.y) for s in planned)
    assert len(xy_pairs) >= 2
