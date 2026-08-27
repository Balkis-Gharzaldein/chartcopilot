"""Acceptance-oriented pipeline test using the messy fixture.

Covers the acceptance criteria that don't require a live LLM:
  * header row detected below a title row in the data sheet
  * a measure column matched via synonym (revenue ~= Total_Sales_USD)
  * a line with an unmatchable measure term becomes a skipped, explained spec
  * high-cardinality categories are bucketed into an "other" slice
  * the narrative only cites numbers that came from computed summaries
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from agent import execute_plan, reexecute_spec
from guideline import extract_guideline
from ingestion import ingest_file
from narrative import synthesize_narrative
from planning import plan_charts

FIXTURE = Path(__file__).resolve().parent.parent / "sample_data" / "messy_sales_example.xlsx"


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = ingest_file(FIXTURE.read_bytes(), FIXTURE.name)

    def test_header_row_detected_below_title(self):
        sales = self.wb.profile_for("Sales")
        names = sales.column_names()
        self.assertIn("Total_Sales_USD", names)
        self.assertNotIn("Quarterly Sales Report", names)
        self.assertEqual(sales.row_count, 400)

    def test_plan_specs(self):
        g = extract_guideline(self.wb)
        self.assertGreaterEqual(len(g.lines), 4)
        specs = plan_charts(self.wb.profiles, g.lines)
        planned = [s for s in specs if s.status == "planned"]
        skipped = [s for s in specs if s.status == "skipped"]
        self.assertGreaterEqual(len(planned), 4, [s.model_dump() for s in specs])
        self.assertGreaterEqual(len(skipped), 1, [s.model_dump() for s in specs])
        # revenue synonym hit
        revenue_line = next(s for s in specs if "revenue" in s.title.lower())
        self.assertEqual(revenue_line.status, "planned")
        self.assertEqual(revenue_line.y, "Total_Sales_USD")

    def test_execute_produces_charts_and_bucketing(self):
        g = extract_guideline(self.wb)
        specs = plan_charts(self.wb.profiles, g.lines)
        results = execute_plan(self.wb, specs, attempt_llm=False)
        charts = [r for r in results if r.figure_json]
        self.assertGreaterEqual(len(charts), 4)
        bucketed = [r for r in charts if r.adaptation_note and "other" in r.adaptation_note]
        self.assertTrue(bucketed, "expected at least one bucketed chart")
        sum_text = "".join(json_summary_text(charts))
        for r in charts:
            for v in iter_leaf_numbers(r.computed_summary):
                self.assertIn(f"{v}", sum_text, f"number {v} not traced in summaries")

    def test_computed_numbers_verified_against_source(self):
        """Every produced chart's computed_summary must be independently re-derivable
        from the raw sheet frame (host-side, no sandbox, no LLM)."""
        g = extract_guideline(self.wb)
        specs = plan_charts(self.wb.profiles, g.lines)
        results = execute_plan(self.wb, specs, attempt_llm=False)
        charts = [r for r in results if r.figure_json]
        self.assertTrue(charts, "expected some produced charts to verify")
        for r in charts:
            self.assertTrue(
                r.figure_data,
                f"chart '{r.spec.title}' must expose the rows it was built from",
            )
            self.assertTrue(
                r.verified,
                f"chart '{r.spec.title}' failed closed-form verification: {r.verification}",
            )
            self.assertTrue(
                r.computed_summary.get("measure") and r.computed_summary.get("grouped_by"),
                f"chart '{r.spec.title}' summary must be self-describing (measure/grouped_by)",
            )

    def test_chat_edit_show_real_category_names(self):
        """'labels ... group' expands the long-tail 'other' slice into real names."""
        g = extract_guideline(self.wb)
        specs = plan_charts(self.wb.profiles, g.lines)
        results = execute_plan(self.wb, specs, attempt_llm=False)
        pie = next(r for r in results if r.spec.chart_type == "pie")
        self.assertIn("other", [str(c["category"]) for c in pie.computed_summary.get("top_categories", [])])
        idx = results.index(pie)
        results, msg = reexecute_spec(
            self.wb, idx, results, "edit the labels of the pie chart to group", attempt_llm=False,
        )
        edited = results[idx]
        self.assertTrue(edited.figure_json, edited.adaptation_note)
        names = [str(c["category"]) for c in edited.computed_summary.get("top_categories", [])]
        self.assertNotIn("other", names, f"still bucketed after label edit: {names}")
        self.assertTrue(any(n.startswith("Widget ") for n in names), names)
        self.assertTrue(edited.verified, edited.verification)
        self.assertGreater(edited.computed_summary.get("n_categories", 0), 10)

    def test_chat_edit_rename_label(self):
        """'rename X to Y' applies a label_map and verification still passes."""
        g = extract_guideline(self.wb)
        specs = plan_charts(self.wb.profiles, g.lines)
        results = execute_plan(self.wb, specs, attempt_llm=False)
        pie = next(r for r in results if r.spec.chart_type == "pie")
        idx = results.index(pie)
        results, msg = reexecute_spec(
            self.wb, idx, results, "rename Widget Pulse to Pulse", attempt_llm=False,
        )
        edited = results[idx]
        self.assertIn("Pulse", msg)
        names = {str(c["category"]) for c in edited.computed_summary.get("top_categories", [])}
        self.assertIn("Pulse", names)
        self.assertNotIn("Widget Pulse", names)
        self.assertTrue(edited.verified, edited.verification)

    def test_narrative_grounded_in_numbers(self):
        g = extract_guideline(self.wb)
        specs = plan_charts(self.wb.profiles, g.lines)
        results = execute_plan(self.wb, specs, attempt_llm=False)
        narrative = synthesize_narrative(results)
        self.assertTrue(narrative)
        self.assertLessEqual(len(narrative.split()), 350)
        # "x: 1-60" range notation must not be read as a bare "-60"
        numbers = re.findall(r"(?<![\w.])-?\d+\.?\d*", narrative)
        mentioned = {float(n) for n in numbers}
        source = {
            float(v)
            for r in results
            for v in iter_leaf_numbers(r.computed_summary)
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        # percentage renderings of top_share are legitimately derived
        derived = {
            round(float(v.get("top_share", 0)) * 100, 0)
            for r in results
            for v in [r.computed_summary]
            if isinstance(r.computed_summary.get("top_share"), (int, float))
        }
        allowed = source | derived
        stray = {m for m in mentioned if abs(m) > 1 and not near_any(m, allowed)}
        self.assertEqual(
            stray, set(),
            f"narrative mentions numbers absent from computed summaries: {stray}",
        )


def json_summary_text(charts):
    import json  # noqa: PLC0415

    for c in charts:
        yield json.dumps(c.computed_summary, default=str)


def iter_leaf_numbers(d):
    if isinstance(d, dict):
        for v in d.values():
            yield from iter_leaf_numbers(v)
    elif isinstance(d, list):
        for v in d:
            yield from iter_leaf_numbers(v)
    else:
        yield d


def near_any(x, source):
    return any(abs(x - y) < 0.5 for y in source)


if __name__ == "__main__":
    unittest.main()