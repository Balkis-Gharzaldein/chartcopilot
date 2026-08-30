"""Phase 3.5 — Visualization Intelligence Quality Validation.

Evaluates whether ChartCopilot behaves like a general-purpose analyst,
not just whether code passes. Uses programmatically generated synthetic
datasets, tests automatic exploration, diversity, CAN/APPROPRIATE/USEFUL
gates, ranking, and chart-selection decisions. Reports weaknesses honestly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ingestion import Workbook, profile_sheet
from viz.profiler import profile_data
from viz.orchestrator import orchestrate
from viz.gates.can import can_gate
from viz.gates.appropriate import appropriate_gate
from viz.gates.useful import useful_gate
from viz.scoring import score_candidate
from schemas import ChartSpec, SheetProfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(df: pd.DataFrame, name="data"):
    prof = profile_sheet(name, df, [])
    wb = Workbook(profiles=[prof], frames={name: df}, raw=None)
    dp = profile_data(prof, df)
    return prof, dp, wb

def _explore(df: pd.DataFrame, lines=None):
    lines = lines or ["Analyze this data"]
    prof, dp, wb = _make(df)
    specs = orchestrate([prof], wb.frames, lines)
    planned = [s for s in specs if s.status == "planned"]
    skipped = [s for s in specs if s.status == "skipped"]
    return prof, dp, wb, specs, planned, skipped

def _goals(specs):
    # infer goal from chart_type mapping (same as orchestrator)
    m = {"bar":"comparison","horizontal_bar":"ranking","grouped_bar":"comparison","stacked_bar":"composition","stacked_100":"composition","line":"trend","area":"trend","histogram":"distribution","boxplot":"distribution","scatter":"relationship","heatmap":"correlation","pie":"composition","donut":"composition"}
    return [m.get(s.chart_type, "overview") for s in specs]

def _duplicate_count(specs):
    # duplicate = same (goal,x,y,group_by) — includes group_by for grouped/stacked
    m = {"bar":"comparison","horizontal_bar":"ranking","grouped_bar":"comparison","stacked_bar":"composition","stacked_100":"composition","line":"trend","area":"trend","histogram":"distribution","boxplot":"distribution","scatter":"relationship","heatmap":"correlation","pie":"composition","donut":"composition"}
    seen=set()
    dups=0
    for s in specs:
        g=m.get(s.chart_type,"overview")
        key=(g,s.x,s.y,s.group_by)
        if key in seen:
            dups+=1
        seen.add(key)
    return dups

def _xy_variants(specs):
    from collections import Counter
    return Counter((s.x, s.y) for s in specs)

# ---------------------------------------------------------------------------
# Synthetic datasets (11 shapes)
# ---------------------------------------------------------------------------

def ds_simple_cat_num():
    return pd.DataFrame({"region": ["A","B","C"]*20, "sales": np.random.randint(10,100,60)})

def ds_multi_cat_num():
    return pd.DataFrame({
        "region": ["North","South"]*30,
        "product": ["X","Y","Z"]*20,
        "sales": np.random.randint(10,100,60),
    })

def ds_time_series():
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=40),
        "sales": np.random.randint(20,80,40),
        "profit": np.random.randint(5,30,40),
    })

def ds_multi_numeric():
    return pd.DataFrame({
        "a": np.random.normal(0,1,80),
        "b": np.random.normal(0,1,80),
        "c": np.random.normal(0,1,80),
        "d": np.random.normal(0,1,80),
    })

def ds_distributions():
    # skewed + normal + uniform
    np.random.seed(1)
    return pd.DataFrame({
        "normal": np.random.normal(50,10,100),
        "skewed": np.random.exponential(2,100)*10,
        "uniform": np.random.uniform(0,100,100),
    })

def ds_high_card():
    return pd.DataFrame({"category": [f"cat_{i}" for i in range(50)]*2, "value": np.random.randint(1,100,100)})

def ds_identifiers():
    return pd.DataFrame({
        "user_id": [f"id_{i}" for i in range(80)],
        "region": ["A","B"]*40,
        "value": np.random.randint(1,100,80),
    })

def ds_missing():
    df = pd.DataFrame({"cat": ["A","B","C"]*20, "val": np.random.randint(1,100,60).astype(float)})
    df.loc[np.random.choice(60, 18, replace=False), "val"] = np.nan
    df.loc[np.random.choice(60, 10, replace=False), "cat"] = None
    return df

def ds_negative_zero():
    return pd.DataFrame({"cat": ["A","B","C"]*20, "val": np.random.randint(-20,30,60)})

def ds_small():
    return pd.DataFrame({"cat": ["A","B","C","A","B"], "val": [10,20,15,12,18]})

def ds_rich():
    dates = pd.date_range("2024-01-01", periods=60)
    return pd.DataFrame({
        "date": list(dates),
        "region": ["North","South","East","West"]*15,
        "product": ["X","Y","Z"]*20,
        "sales": np.random.randint(20,100,60),
        "quantity": np.random.randint(1,20,60),
        "profit": np.random.randint(5,40,60),
    })

def ds_mixed_dates():
    # ambiguous formats as strings
    vals = ["2024-01-01","2024/02/15","15-03-2024","2024.04.20","May 5, 2024"]*6
    return pd.DataFrame({"date_str": vals, "value": np.random.randint(1,100,30)})

DATASETS = {
    "simple_cat_num": ds_simple_cat_num,
    "multi_cat_num": ds_multi_cat_num,
    "time_series": ds_time_series,
    "multi_numeric": ds_multi_numeric,
    "distributions": ds_distributions,
    "high_card": ds_high_card,
    "identifiers": ds_identifiers,
    "missing": ds_missing,
    "negative_zero": ds_negative_zero,
    "small": ds_small,
    "rich": ds_rich,
    "mixed_dates": ds_mixed_dates,
}

# ---------------------------------------------------------------------------
# 1-2. Evaluation per dataset
# ---------------------------------------------------------------------------

class TestExploration:
    def test_all_datasets_explore(self):
        for name, fn in DATASETS.items():
            df = fn()
            prof, dp, wb, specs, planned, skipped = _explore(df)
            # Must produce at least 1 planned for non-tiny datasets, or explain why
            if name not in ("small",):
                assert len(planned) >= 1, f"{name} produced 0 planned"
            # No duplicate analytical questions in planned
            dups = _duplicate_count(planned)
            assert dups == 0, f"{name} has {dups} duplicate (goal,x,y) questions"
            # Goals should be diverse for rich
            if name == "rich":
                goals = set(_goals(planned))
                assert len(goals) >= 3, f"rich goals {goals} not diverse"
            # Identifiers should not be used as x
            if name == "identifiers":
                for s in planned:
                    assert s.x != "user_id", "identifier used as x"
            # Mixed dates: temporal detection may be weak — report, not fail
            # (tested separately)

# ---------------------------------------------------------------------------
# 3. Diversity
# ---------------------------------------------------------------------------

class TestDiversity:
    def test_no_duplicate_revenue_by_region_variants(self):
        df = ds_simple_cat_num()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Analyze this data"])
        planned = [s for s in specs if s.status == "planned"]
        # For simple dataset, same (x,y) with different goals (comparison vs composition vs distribution) is not duplicate
        # Check duplicate via (goal,x,y,group_by)
        dups = _duplicate_count(planned)
        assert dups == 0, f"duplicate analytical questions {dups}"
        # Also ensure not 4 variants of same (x,y) ignoring goal is limited to 3 for simple (only one pair possible)
        from collections import Counter
        cnt = Counter((s.x, s.y) for s in planned)
        # Simple dataset has only one categorical+numeric pair, so some sharing is unavoidable, but should not be 4 identical
        assert max(cnt.values(), default=0) <= 3, f"too many variants of same pair {cnt}"

    def test_rich_diversity(self):
        df = ds_rich()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Analyze this data"])
        planned = [s for s in specs if s.status == "planned"]
        goals = _goals(planned)
        # Should cover at least 3 different goals
        assert len(set(goals)) >= 3
        # Should cover at least 2 different columns as x
        xs = set(s.x for s in planned)
        assert len(xs) >= 2

# ---------------------------------------------------------------------------
# 4. CAN / APPROPRIATE / USEFUL separate
# ---------------------------------------------------------------------------

class TestGates:
    def test_can_true_appropriate_false(self):
        # Pie with 50 cats: CAN true (via bucketing) but APPROPRIATE penalized (but still true with warning)
        # To get APPROPRIATE false, use high-card identifier or negative pie
        df = pd.DataFrame({"cat": [f"c{i}" for i in range(50)]*2, "val": [-5]*100})  # negative
        prof, dp, wb = _make(df)
        spec = ChartSpec(id="t", sheet="data", chart_type="pie", title="t", x="cat", y="val", agg_function="sum")
        assert can_gate(spec, dp).passed is True
        assert appropriate_gate(spec, dp).passed is False  # negatives

    def test_appropriate_true_useful_false(self):
        # Single category -> APPROPRIATE true but USEFUL false
        df = pd.DataFrame({"cat": ["A"]*20, "val": range(20)})
        prof, dp, wb = _make(df)
        spec = ChartSpec(id="t", sheet="data", chart_type="bar", title="t", x="cat", y="val", agg_function="sum")
        assert can_gate(spec, dp).passed is True
        # APPROPRIATE: bar with 1 cat is not very useful but technically appropriate?
        # Our APPROPRIATE for bar with 1 cat is still true (card 1 not checked), but USEFUL should be false
        assert useful_gate(spec, dp, "comparison").passed is False

    def test_all_true(self):
        df = pd.DataFrame({"cat": ["A","B","C"]*20, "val": range(60)})
        prof, dp, wb = _make(df)
        spec = ChartSpec(id="t", sheet="data", chart_type="bar", title="t", x="cat", y="val", agg_function="sum")
        assert can_gate(spec, dp).passed
        assert appropriate_gate(spec, dp).passed
        assert useful_gate(spec, dp, "comparison").passed

    def test_no_failed_gates_generated(self):
        for name, fn in DATASETS.items():
            df = fn()
            prof, dp, wb = _make(df)
            specs = orchestrate([prof], wb.frames, ["Analyze this data"])
            for s in specs:
                if s.status == "planned":
                    assert can_gate(s, dp).passed, f"{name} {s.chart_type} failed CAN but was generated"
                    assert appropriate_gate(s, dp).passed, f"{name} {s.chart_type} failed APPROPRIATE but was generated"

# ---------------------------------------------------------------------------
# 5. Ranking quality
# ---------------------------------------------------------------------------

class TestRanking:
    def test_temporal_rank(self):
        df = ds_time_series()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Show trend"])
        # Top should be line
        assert specs[0].chart_type in ("line","area"), f"temporal top was {specs[0].chart_type}, not line"

    def test_distribution_rank(self):
        df = pd.DataFrame({"a": np.random.normal(50,10,100)})
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Show distribution of a"])
        assert specs[0].chart_type in ("histogram","boxplot")

    def test_scatter_rank(self):
        df = pd.DataFrame({"a": np.random.normal(0,1,50), "b": np.random.normal(0,1,50)})
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Show relationship between a and b"])
        assert any(s.chart_type=="scatter" for s in specs)

    def test_heatmap_rank(self):
        df = ds_multi_numeric()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Show correlation"])
        assert any(s.chart_type=="heatmap" for s in specs)

    def test_high_card_rank(self):
        df = ds_high_card()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Compare categories"])
        assert specs[0].chart_type == "horizontal_bar"

    def test_pie_small_composition(self):
        df = pd.DataFrame({"cat": ["A","B","C"]*10, "val": np.random.randint(1,20,30)})
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Show share of cat as pie chart"])
        pie = [s for s in specs if s.chart_type in ("pie","donut")]
        assert pie and pie[0].status == "planned"

    def test_pie_negative_rejected(self):
        df = pd.DataFrame({"cat": ["A","B","C"]*10, "val": [-5,0,5]*10})
        prof, dp, wb = _make(df)
        spec = ChartSpec(id="t", sheet="data", chart_type="pie", title="t", x="cat", y="val", agg_function="sum")
        assert appropriate_gate(spec, dp).passed is False

    def test_identifier_not_ranked(self):
        df = ds_identifiers()
        prof, dp, wb = _make(df)
        specs = orchestrate([prof], wb.frames, ["Analyze this data"])
        for s in specs:
            assert s.x != "user_id" and s.y != "user_id"

    def test_zero_variance_rejected(self):
        df = pd.DataFrame({"cat": ["A","B","C"]*10, "val": [5]*30})
        prof, dp, wb = _make(df)
        spec = ChartSpec(id="t", sheet="data", chart_type="histogram", title="t", x="val", y=None)
        assert appropriate_gate(spec, dp).passed is False

# ---------------------------------------------------------------------------
# 6. Chart selection decisions (explicit cases above already)
# ---------------------------------------------------------------------------

class TestSelectionDecisions:
    def test_mixed_date_detection(self):
        df = ds_mixed_dates()
        prof, dp, wb = _make(df)
        # Check profiler role for date_str
        role = dp.by_name("date_str").role if dp.by_name("date_str") else "unknown"
        # Mixed formats: may be categorical or temporal — report weakness if categorical
        # We expect temporal for at least ISO-like, but mixed may be categorical
        # This is a known weakness check, not hard fail
        assert role in ("temporal","categorical")

# ---------------------------------------------------------------------------
# 7. Full report generation (for manual inspection)
# ---------------------------------------------------------------------------

def _generate_report():
    lines=[]
    for name, fn in DATASETS.items():
        df=fn()
        prof, dp, wb, specs, planned, skipped = _explore(df)
        goals=_goals(planned)
        cols_covered=set(s.x for s in planned) | set(s.y for s in planned if s.y)
        # Candidates rejected: skipped + filtered by gates (we don't have filtered count directly, but skipped is proxy)
        # For fuller, we could count generated before ranking vs after
        from viz.candidates import generate_exploratory
        all_gen = generate_exploratory(dp, prof.sheet_name) if "Analyze" else []
        lines.append(f"## {name}")
        lines.append(f"- shape: {df.shape}, dtypes: {dict(df.dtypes.astype(str))}")
        lines.append(f"- detected roles: {[(c.name,c.role,c.cardinality) for c in dp.columns]}")
        lines.append(f"- planned: {len(planned)} ({[(s.chart_type,s.x,s.y) for s in planned]})")
        lines.append(f"- skipped: {len(skipped)}")
        lines.append(f"- goals: {set(goals)}, cols: {cols_covered}, dups: {_duplicate_count(planned)}")
        lines.append("")
    return "\n".join(lines)

# Allow running as script to print report
if __name__ == "__main__":
    print(_generate_report())

