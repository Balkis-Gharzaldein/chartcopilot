"""Streamlit entrypoint for ChartCopilot.

Upload an .xlsx file (data sheets + optional Instructions sheet), review the
schema, click Run, then browse the dashboard: charts in guideline order, a
grounded narrative, and a chat box to edit a single chart in place.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import plotly.io as pio
import streamlit as st

from agent import execute_plan, reexecute_spec, resolve_edit
from guideline import extract_guideline
from ingestion import ingest_file
from narrative import synthesize_narrative
from planning import plan_charts

def _to_fig(figure_json: str):
    try:
        return pio.from_json(figure_json)
    except Exception:
        return None


def _has_llm() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )


st.set_page_config(page_title="ChartCopilot", layout="wide")
st.title("ChartCopilot")
st.caption("Guideline-driven Excel visualization agent — upload a workbook, get a dashboard.")

if not _has_llm():
    st.info(
        "No Anthropic/OpenAI/Gemini API key found. Running in **deterministic mode**: planning, "
        "snippet generation and narrative use internal heuristics. Set ANTHROPIC_API_KEY, "
        "OPENAI_API_KEY, or GEMINI_API_KEY to enable the full agentic pipeline.",
        icon="ℹ️",
    )

uploaded = st.file_uploader("Upload an Excel file", type=["xlsx", "xls", "csv"])

if uploaded is None:
    st.stop()


# --- persist the parsed workbook across reruns ---------------------------------
key = f"wb_{uploaded.name}"
if key not in st.session_state:
    try:
        st.session_state[key] = ingest_file(uploaded.getvalue(), uploaded.name)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not parse the file: {exc}")
        st.stop()
workbook = st.session_state[key]

# --- schema summary --------------------------------------------------------------
st.subheader("Schema overview")
schema_rows = [
    {"Sheet": p.sheet_name, "Rows": p.row_count, "Columns": len(p.columns)}
    for p in workbook.profiles
]
st.dataframe(pd.DataFrame(schema_rows), use_container_width=True)
with st.expander("Column details"):
    for p in workbook.profiles:
        st.markdown(f"**{p.sheet_name}** — {p.row_count} rows")
        cols = pd.DataFrame(
            {
                "column": [c.name for c in p.columns],
                "dtype": [c.dtype for c in p.columns],
                "nulls": [c.null_count for c in p.columns],
                "unique": [c.unique_count for c in p.columns],
                "samples": [", ".join(c.sample_values[:3]) for c in p.columns],
            }
        )
        st.dataframe(cols, use_container_width=True, height=min(360, 40 * len(p.columns) + 40))

# --- guideline ---------------------------------------------------------------------
sheet_names = [p.sheet_name for p in workbook.profiles]
has_inst = any("instruction" in s.lower() or "guideline" in s.lower() for s in sheet_names)

text_area = None
if has_inst:
    st.caption(f"Instructions sheet detected: **{[s for s in sheet_names if 'instruction' in s.lower() or 'guideline' in s.lower()]}**")
    with st.expander("Add extra guideline lines (optional supplement)"):
        text_area = st.text_area("Extra guideline lines (one per line)", key="supp", height=90)
else:
    st.markdown("No Instructions sheet detected — enter your guideline below (one request per line).")
    text_area = st.text_area("Guideline text", key="guide", height=160)

line_data = extract_guideline(workbook, text_area or "")


def run_pipeline():
    with st.spinner("Planning chart specs…"):
        specs = plan_charts(workbook.profiles, line_data.lines, frames=workbook.frames)
    st.session_state.pending_specs = specs
    progress_box = st.empty()
    progress_box.caption("Preparing the sandbox…")

    def on_progress(done, total, spec):
        progress_box.caption(f"Executing chart {done}/{total}: {spec.title}")

    with st.spinner("Executing charts in the sandboxed agent loop…"):
        results = execute_plan(workbook, specs, attempt_llm=_has_llm(), progress=on_progress)
    progress_box.caption(f"Executed {len(results)} chart specs.")
    st.session_state.results = results
    with st.spinner("Synthesizing narrative…"):
        st.session_state.narrative = synthesize_narrative(results)
    st.session_state.run_done = True


if st.button("Run", type="primary", disabled=not line_data.lines):
    if not line_data.lines:
        st.warning("No guideline lines given — add an Instructions sheet or text above.")
    else:
        run_pipeline()

if not st.session_state.get("run_done"):
    if not line_data.lines:
        st.caption("Nothing to run yet: no guideline lines present.")
    st.stop()

results = st.session_state.results
specs = st.session_state.pending_specs

# --- plan preview ------------------------------------------------------------------
with st.expander("View chart plan (including skipped items)"):
    for i, s in enumerate(specs, start=1):
        if s.status == "skipped":
            st.markdown(f"**{i}. `{s.id}`** — *skipped*: {s.skip_reason}")
        else:
            st.markdown(
                f"**{i}. `{s.id}`** — {s.chart_type} on `{s.sheet}` "
                f"(x={s.x}, y={s.y})"
                + (f", group_by={s.group_by}" if s.group_by else "")
                + (f", agg={s.agg_function}" if s.agg_function else "")
            )

# --- narrative -----------------------------------------------------------------------
st.subheader("Summary")
narrative = st.session_state.get("narrative", "")
st.write(narrative)

# --- charts --------------------------------------------------------------------------
st.subheader("Charts")
skipped_text = []
produced = 0
for i, r in enumerate(results, start=1):
    if r.skipped:
        skipped_text.append(f"- `{r.spec.id}` ({r.spec.title}): {r.spec.skip_reason}")
        continue
    col = st.container()
    col.markdown(f"### {i}. {r.spec.title}")
    if r.figure_json:
        fig = _to_fig(r.figure_json)
        if fig is not None:
            col.plotly_chart(fig, use_container_width=True)
            produced += 1
        else:
            col.warning("Figure could not be rendered.")
    else:
        col.warning(r.adaptation_note or "No figure produced.")
    if r.adaptation_note:
        col.caption(f"Note: {r.adaptation_note}")

    if r.verified:
        col.success("Verified against source data: independent recomputation of totals/categories matched.")
    elif r.verification:
        if "error" in r.verification:
            col.caption(f"Verification not applicable: {r.verification['error']}")
        else:
            failed = r.verification.get("failed") or []
            col.warning(f"Verification failed on: {', '.join(failed)}")

    # --- semantic validation results ---
    if r.validation:
        val_warnings = r.validation.get("warnings", [])
        val_errors = r.validation.get("errors", [])
        if val_errors:
            col.warning(f"Semantic validation: {', '.join(val_errors)}")
        if val_warnings:
            for w in val_warnings:
                col.caption(f"Validation note: {w}")
    with col.expander("Chart data (rows used)"):
        if r.figure_data:
            st.dataframe(pd.DataFrame(r.figure_data), use_container_width=True)
        else:
            st.caption("No tabular rows were produced for this chart.")
    with col.expander("Computed numbers"):
        summary = r.computed_summary or {}
        if summary:
            measure = summary.get("measure")
            grouped_by = summary.get("grouped_by")
            label = ""
            if measure:
                label += f"**measure:** `{measure}`"
            if grouped_by:
                label += ("  ·  " if label else "") + f"**grouped by:** `{grouped_by}`"
            if label:
                st.write(label)
        st.json(summary)

    # --- recommendations: related chart suggestions ---
    if r.recommendations:
        with col.expander(f"Related chart suggestions ({len(r.recommendations)})"):
            for rec in r.recommendations:
                st.markdown(f"- **{rec.spec.chart_type}**: {rec.spec.title}")
            st.caption("Use the chat below to change the chart type (e.g. 'make it a pie chart').")

if skipped_text:
    with st.expander(f"Skipped during planning ({len(skipped_text)} — shown, not hidden)"):
        for t in skipped_text:
            st.markdown(t)

if produced == 0 and not skipped_text:
    st.write("No charts could be rendered for this guideline.")

# --- chat refinement ---------------------------------------------------------------------
st.subheader("Refine a chart")
st.caption('Ask to change a chart with a message like "make the revenue line a bar chart".')

for msg in st.session_state.get("chat", []):
    with st.chat_message("user"):
        st.write(msg[0])
    with st.chat_message("assistant"):
        st.write(msg[1])

prompt = st.chat_input("Edit a chart (e.g. 'make chart 2 a pie chart')")
if prompt:
    target, note = resolve_edit(prompt, results)
    reply_parts = [note]
    changed = None
    if target is not None:
        idx = results.index(target)
        results, change_msg = reexecute_spec(workbook, idx, results, prompt, attempt_llm=_has_llm())
        st.session_state.results = results
        st.session_state.narrative = synthesize_narrative(results)
        reply_parts.append(change_msg)
    reply = "\n".join(reply_parts)
    st.session_state.setdefault("chat", []).append((prompt, reply))
    st.rerun()