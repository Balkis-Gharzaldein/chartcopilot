"""Generate Phase 3.5 quality report — per dataset evaluation."""

import pandas as pd
import numpy as np
from ingestion import profile_sheet, Workbook
from viz.profiler import profile_data
from viz.orchestrator import orchestrate
from viz.gates.can import can_gate
from viz.gates.appropriate import appropriate_gate
from viz.gates.useful import useful_gate
from schemas import ChartSpec

def _make(df):
    prof = profile_sheet("data", df, [])
    wb = Workbook(profiles=[prof], frames={"data": df}, raw=None)
    dp = profile_data(prof, df)
    return prof, dp, wb

def eval_dataset(name, df, lines=None):
    lines = lines or ["Analyze this data"]
    prof, dp, wb = _make(df)
    specs = orchestrate([prof], wb.frames, lines)
    planned = [s for s in specs if s.status=="planned"]
    skipped = [s for s in specs if s.status=="skipped"]
    # candidates before ranking? Use generate_exploratory count
    from viz.candidates import generate_exploratory
    all_gen = generate_exploratory(dp, "data")
    # gate details for planned
    details=[]
    for s in planned:
        c = can_gate(s, dp)
        a = appropriate_gate(s, dp)
        u = useful_gate(s, dp, "overview")
        details.append((s.chart_type, s.x, s.y, s.group_by, c.passed, a.passed, u.passed, s.data_notes))
    goals = {"bar":"comparison","horizontal_bar":"ranking","grouped_bar":"comparison","stacked_bar":"composition","stacked_100":"composition","line":"trend","area":"trend","histogram":"distribution","boxplot":"distribution","scatter":"relationship","heatmap":"correlation","pie":"composition","donut":"composition"}
    goal_set = set(goals.get(s.chart_type,"overview") for s in planned)
    cols_covered = set(s.x for s in planned) | set(s.y for s in planned if s.y)
    # duplicate check
    seen=set()
    dups=0
    for s in planned:
        g=goals.get(s.chart_type,"overview")
        key=(g,s.x,s.y,s.group_by)
        if key in seen:
            dups+=1
        seen.add(key)
    return {
        "name": name,
        "shape": df.shape,
        "roles": [(c.name,c.role,c.cardinality) for c in dp.columns],
        "all_gen": len(all_gen),
        "planned": planned,
        "skipped": skipped,
        "goals": goal_set,
        "cols_covered": cols_covered,
        "dups": dups,
        "details": details,
    }

# Build datasets
np.random.seed(0)
datasets = {
    "simple_cat_num": pd.DataFrame({"region": ["A","B","C"]*20, "sales": np.random.randint(10,100,60)}),
    "multi_cat_num": pd.DataFrame({"region": ["North","South"]*30, "product": ["X","Y","Z"]*20, "sales": np.random.randint(10,100,60)}),
    "time_series": pd.DataFrame({"date": pd.date_range("2024-01-01", periods=40), "sales": np.random.randint(20,80,40), "profit": np.random.randint(5,30,40)}),
    "multi_numeric": pd.DataFrame({"a": np.random.normal(0,1,80), "b": np.random.normal(0,1,80), "c": np.random.normal(0,1,80), "d": np.random.normal(0,1,80)}),
    "distributions": pd.DataFrame({"normal": np.random.normal(50,10,100), "skewed": np.random.exponential(2,100)*10, "uniform": np.random.uniform(0,100,100)}),
    "high_card": pd.DataFrame({"category": [f"cat_{i}" for i in range(50)]*2, "value": np.random.randint(1,100,100)}),
    "identifiers": pd.DataFrame({"user_id": [f"id_{i}" for i in range(80)], "region": ["A","B"]*40, "value": np.random.randint(1,100,80)}),
    "missing": (lambda df: df.assign(val=df["val"].where(np.random.rand(60)>0.3)))(pd.DataFrame({"cat": ["A","B","C"]*20, "val": np.random.randint(1,100,60).astype(float)})),
    "negative_zero": pd.DataFrame({"cat": ["A","B","C"]*20, "val": np.random.randint(-20,30,60)}),
    "small": pd.DataFrame({"cat": ["A","B","C","A","B"], "val": [10,20,15,12,18]}),
    "rich": pd.DataFrame({"date": list(pd.date_range("2024-01-01", periods=60)), "region": ["North","South","East","West"]*15, "product": ["X","Y","Z"]*20, "sales": np.random.randint(20,100,60), "quantity": np.random.randint(1,20,60), "profit": np.random.randint(5,40,60)}),
    "mixed_dates": pd.DataFrame({"date_str": ["2024-01-01","2024/02/15","15-03-2024","2024.04.20","May 5, 2024"]*6, "value": np.random.randint(1,100,30)}),
}

reports=[]
for name, df in datasets.items():
    r = eval_dataset(name, df)
    reports.append(r)
    print(f"## {name}")
    print(f"shape: {r['shape']}, roles: {r['roles']}")
    print(f"all_gen: {r['all_gen']}, planned: {len(r['planned'])} {[(s.chart_type,s.x,s.y) for s in r['planned']]}")
    print(f"skipped: {len(r['skipped'])}")
    print(f"goals: {r['goals']}, cols: {r['cols_covered']}, dups: {r['dups']}")
    for d in r['details']:
        print(f"  {d[0]} x={d[1]} y={d[2]} g={d[3]} CAN={d[4]} APP={d[5]} USE={d[6]} score={d[7][:50] if d[7] else ''}")
    print("")

# Overall assessment
print("=== OVERALL ===")
# Count good vs bad
for r in reports:
    print(r['name'], "planned", len(r['planned']), "goals", len(r['goals']), "dups", r['dups'], "cols", len(r['cols_covered']))
