"""Headless end-to-end smoke test of the full pipeline (deterministic mode)."""

from __future__ import annotations

from pathlib import Path

from agent import execute_plan
from guideline import extract_guideline
from ingestion import ingest_file
from narrative import synthesize_narrative
from planning import plan_charts

FIXTURE = Path(__file__).resolve().parent / "sample_data" / "messy_sales_example.xlsx"


def main() -> None:
    print(">>> ingesting", FIXTURE.name)
    wb = ingest_file(FIXTURE.read_bytes(), FIXTURE.name)
    for p in wb.profiles:
        print(f"    sheet '{p.sheet_name}': {p.row_count} rows, {len(p.columns)} cols")

    print(">>> extracting guideline")
    g = extract_guideline(wb)
    print("    source:", g.source, "| lines:", len(g.lines))
    for ln in g.lines:
        print("      -", ln)

    print(">>> planning")
    specs = plan_charts(wb.profiles, g.lines)
    for s in specs:
        flag = "SKIP" if s.status == "skipped" else "ok  "
        print(f"    [{flag}] {s.id} {s.chart_type} x={s.x} y={s.y} agg={s.agg_function}")
        if s.status == "skipped":
            print(f"          reason: {s.skip_reason}")

    print(">>> executing in sandboxed agent loop")
    results = execute_plan(wb, specs, attempt_llm=False)
    for r in results:
        ok = "chart" if r.figure_json else ("skip " if r.skipped else "FAIL ")
        print(f"    [{ok}] {r.spec.id} {r.spec.title[:45]}")
        if r.adaptation_note:
            print(f"          note: {r.adaptation_note}")

    print(">>> narrative")
    text = synthesize_narrative(results)
    print(text)
    print(f"    (words: {len(text.split())})")

    n_charts = sum(1 for r in results if r.figure_json)
    n_skip = sum(1 for r in results if r.skipped)
    n_fail = sum(1 for r in results if not r.figure_json and not r.skipped)
    bucketed = [r for r in results if r.adaptation_note and "other" in (r.adaptation_note or "")]
    print(">>> verdict")
    print(f"    charts={n_charts} skipped={n_skip} failed={n_fail} bucketed={'yes' if bucketed else 'no'}")
    if n_fail:
        print("    NOTE: some planned charts failed.")


if __name__ == "__main__":
    main()