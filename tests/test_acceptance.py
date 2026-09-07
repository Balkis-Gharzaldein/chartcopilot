"""Acceptance battery on the anonymized real-data fixture (Option A merge gate).

Uses tests/fixtures/intl_sales_sample.csv — a stratified, business-name
anonymized slice of a real 37k-row sales export that preserves every
behavior-relevant wart: month-shifted CUSTOMER values, text-stored numerics
(PCS/RATE/GROSS AMT as object dtype), mixed Months formats, 172-category
CUSTOMER cardinality, and a bare integer `index` column.

Contract under test (deterministic-first, LLM force-disabled):
- named columns resolve to real columns (never DATE-for-CUSTOMER swaps),
- text numerics are visible as measures, `index` never is,
- high-cardinality named dimensions are plannable (top-N),
- unanswerable questions skip with Did-you-mean, never empty charts,
- dual questions yield 2 verified charts sharing one top-N rank,
- dirty dimension values are quarantined + flagged, verification agrees.

If any test goes red, fix against THIS fixture — never against synthetic
replicas. LLM paths are monkeypatched off so the battery is reproducible
with or without provider keys in the environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import execute_plan
from ingestion import Workbook, ingest_file
from planning import plan_charts

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "intl_sales_sample.csv"


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "available_provider", lambda: None)


@pytest.fixture(scope="module")
def wb() -> Workbook:
    return ingest_file(FIXTURE.read_bytes(), FIXTURE.name)


def _plan(wb: Workbook, question: str):
    return plan_charts(wb.profiles, [question], frames=wb.frames)


def _planned(specs, **kwargs):
    return [s for s in specs if s.status == "planned"
            and all(getattr(s, k) == v for k, v in kwargs.items())]


def test_fixture_preserves_real_dirt(wb: Workbook):
    prof = wb.profile_for("data")
    names = prof.column_names()
    assert "CUSTOMER" in names and "GROSS AMT" in names
    cust = next(c for c in prof.columns if c.name == "CUSTOMER")
    assert cust.unique_count > 100, "fixture must keep high-cardinality CUSTOMER"
    gross = next(c for c in prof.columns if c.name == "GROSS AMT")
    assert gross.dtype == "object", "measures must stay text-stored (object)"


def test_trend_line_revenue_by_month(wb: Workbook):
    specs = _plan(wb, "Total revenue by month as a line chart")
    line = _planned(specs, chart_type="line", y="GROSS AMT")
    assert line, [s.model_dump() for s in specs]
    assert line[0].x in ("DATE", "Months")
    res = execute_plan(wb, [line[0]], attempt_llm=False)[0]
    assert res.figure_json and res.verified, res.verification


def test_ranking_top_styles_by_pcs(wb: Workbook):
    specs = _plan(wb, "Top 5 Styles by PCS")
    bars = _planned(specs, x="Style", y="PCS")
    assert bars, [s.model_dump() for s in specs]
    assert "top 5" in (bars[0].data_notes or "").lower()
    res = execute_plan(wb, [bars[0]], attempt_llm=False)[0]
    assert res.figure_json and res.verified, res.verification


def test_composition_pie_revenue_by_size(wb: Workbook):
    specs = _plan(wb, "Share of revenue by Size as a pie")
    pies = _planned(specs, chart_type="pie", x="Size", y="GROSS AMT")
    assert pies, [s.model_dump() for s in specs]


def test_distribution_rate_histogram(wb: Workbook):
    specs = _plan(wb, "Show the distribution of RATE")
    hists = _planned(specs, chart_type="histogram", x="RATE")
    assert hists, [s.model_dump() for s in specs]


def test_relationship_pcs_rate_scatter(wb: Workbook):
    specs = _plan(wb, "Show relationship between PCS and RATE")
    sc = [s for s in specs if s.status == "planned" and s.chart_type == "scatter"
          and {s.x, s.y} == {"PCS", "RATE"}]
    assert sc, [s.model_dump() for s in specs]


def test_monthly_trend_with_time_hints(wb: Workbook):
    specs = _plan(wb, "Monthly revenue over the last 2 years")
    got = [s for s in specs if s.status == "planned" and s.y == "GROSS AMT"]
    assert got, [s.model_dump() for s in specs]
    assert got[0].x in ("DATE", "Months")
    # Monthly bucketing must be detected; last-N window filters are a known
    # gap in the deterministic intent layer (tracked separately).
    assert "monthly" in (got[0].data_notes or "").lower()


def test_unanswerable_skips_transparently(wb: Workbook):
    specs = _plan(wb, "profit margin by product")
    skipped = [s for s in specs if s.status == "skipped"]
    assert skipped, [s.model_dump() for s in specs]
    reason = skipped[0].skip_reason or ""
    assert "Available columns" in reason, reason
    assert not [s for s in specs if s.status == "planned" and s.figure_json], \
        "skipped specs must not carry figures"


def test_dual_top10_revenue_and_units(wb: Workbook):
    specs = _plan(
        wb, "What are the top 10 customers by revenue, and how many units did each buy?")
    planned = [s for s in specs if s.status == "planned"]
    by_y = {s.y for s in planned}
    assert {"GROSS AMT", "PCS"} <= by_y, [s.model_dump() for s in specs]
    assert all(s.x == "CUSTOMER" for s in planned)
    results = execute_plan(wb, planned, attempt_llm=False)
    figs = [r for r in results if r.figure_json]
    assert len(figs) == 2
    for r in figs:
        assert r.verified, (r.spec.y, r.verification)
        assert (r.validation or {}).get("warnings") == [], r.validation
        assert "misaligned" in (r.adaptation_note or ""), r.adaptation_note
    sets = [{row.get("CUSTOMER") for row in (r.figure_data or [])} for r in figs]
    assert sets[0] == sets[1] and len(sets[0]) == 10


def test_generic_trend_never_uses_index(wb: Workbook):
    specs = _plan(wb, "Show trend")
    planned = [s for s in specs if s.status == "planned"]
    assert planned, [s.model_dump() for s in specs]
    for s in planned:
        assert s.y != "index" and s.x != "index", s.model_dump()
