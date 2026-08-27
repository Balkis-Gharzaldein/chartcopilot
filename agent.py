"""ReAct-style agent loop.

For each planned ChartSpec the agent may use three tools -- inspect_data,
run_code (sandboxed), create_chart -- with an LLM in the loop writing the pandas
snippet and reacting to errors (retry with corrected code).  Without an API key
it falls back to a deterministic code generator that still runs through the same
sandboxed tools.  Skipped specs pass straight through, untouched.
"""

from __future__ import annotations

import re
from typing import Sequence

import pandas as pd
from pydantic import BaseModel

from ingestion import Workbook
from llm import LLMError, llm_structured
from schemas import ChartResult, ChartSpec, SheetProfile
from tools import create_chart, inspect_data, run_code
from tools.create_chart import ChartBuildError

MAX_ATTEMPTS = 2

AGENT_SYSTEM_PROMPT = (
    "You are the execution engine of an agentic chart-builder. You are given one "
    "ChartSpec and the schema of its sheet. Write a single Python snippet using "
    "a pandas DataFrame already loaded as `df`. The snippet MUST end by assigning "
    "its output to a variable named `result` (a DataFrame or scalar). "
    "Rules: use only pandas (`pd`) and the `df` object; no imports, no file or "
    "network access, no eval/exec/open; do not touch dunder attributes. "
    "If the spec contains data_notes, follow those instructions for data transformation "
    "(e.g. split comma-separated values into separate rows using str.split + explode, "
    "use nunique() for count-distinct, apply top-N limits). "
    "For a bar/pie chart produce one row per category with the aggregated value. "
    "For a line chart produce one row per time point (aggregated if needed). "
    "For a scatter chart produce the raw x/y rows. Reply with JSON {\"code\": \"...\"} "
    "containing only the snippet."
)


class CodeResult(BaseModel):
    code: str


def _translate_filter(desc: str, df: pd.DataFrame):
    m = re.match(r"^\s*([\w\s]+?)\s*(==|!=|>=|<=|>|<|=)\s*(.+?)\s*$", desc)
    if not m:
        return None
    col_name, op, val = m.groups()
    for c in df.columns:
        if c.lower() == col_name.strip().lower():
            col_name = c
            break
    else:
        return None
    if col_name not in df.columns:
        return None
    if val.strip().replace(".", "", 1).isdigit():
        val_out = float(val.strip()) if "." in val else int(val.strip())
    elif val.strip().startswith('"') or val.strip().startswith("'"):
        val_out = val.strip().strip('"').strip("'")
    else:
        val_out = val.strip()
    if isinstance(val_out, str):
        return (f"df[{col_name!r}]", f"{op} {val_out!r}")
    return (f"df[{col_name!r}]", f"{op} {val_out}")


def _fg(col: str) -> str:
    """Python-compatible string literal for a column name."""
    return repr(col)


def _codegen_deterministic(spec: ChartSpec, df: pd.DataFrame) -> str:
    agg = spec.agg_function or "sum"
    x, y = spec.x, spec.y
    cols = list(df.columns)
    notes = (spec.data_notes or "").lower()

    if not y and cols:
        numeric = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
        y = numeric[0] if numeric else None

    chunks: list[str] = []
    if spec.filter:
        cond = _translate_filter(spec.filter, df)
        if cond:
            chunks.append(f"df = df[{cond[0]}] {cond[1]}]")

    # --- data_notes: split / explode ---
    if "split" in notes or "comma" in notes or "explode" in notes:
        xcol = x or cols[0]
        chunks.append(
            f"df = df[df[{_fg(xcol)}].notna()].copy()"
        )
        chunks.append(
            f"df[{_fg(xcol)}] = df[{_fg(xcol)}].astype(str).str.split(',')"
        )
        chunks.append(f"df = df.explode({_fg(xcol)})")
        chunks.append(f"df[{_fg(xcol)}] = df[{_fg(xcol)}].str.strip()")
        chunks.append(f"df = df[df[{_fg(xcol)}] != '']")

    # --- data_notes: count distinct / nunique ---
    use_nunique = ("count distinct" in notes or "nunique" in notes or "unique" in notes
                   or agg == "count_distinct")

    if spec.chart_type == "scatter":
        xs, ys = x or cols[0], y or (cols[1] if len(cols) > 1 else cols[0])
        chunks.append(f"result = df[[{_fg(xs)}, {_fg(ys)}]].dropna()")
        return "\n".join(chunks)

    if spec.chart_type == "line":
        xcol = x or cols[0]
        if use_nunique and y and y in cols:
            if spec.group_by and spec.group_by in cols:
                chunks.append(
                    f"result = df.groupby([{_fg(xcol)}, {_fg(spec.group_by)}])"
                    f"[{_fg(y)}].nunique().reset_index(name='count')"
                )
            else:
                chunks.append(
                    f"result = df.sort_values({_fg(xcol)}).groupby({_fg(xcol)})"
                    f"[{_fg(y)}].nunique().reset_index(name='count')"
                )
        elif spec.group_by and spec.group_by in cols:
            chunks.append(
                f"result = df.groupby([{_fg(xcol)}, {_fg(spec.group_by)}])"
                f"[{_fg(y)}].agg('{agg}').reset_index()"
            )
        else:
            chunks.append(
                f"result = df.sort_values({_fg(xcol)}).groupby({_fg(xcol)})"
                f"[{_fg(y)}].agg('{agg}').reset_index()"
            )
        return "\n".join(chunks)

    # bar / horizontal_bar / pie
    xcol = x or cols[0]
    if use_nunique and y and y in cols:
        chunks.append(
            f"result = df.groupby({_fg(xcol)})[{_fg(y)}].nunique().reset_index(name='count')"
        )
    elif agg == "count" or not y:
        chunks.append(f"result = df.groupby({_fg(xcol)}).size().reset_index(name='count')")
    else:
        chunks.append(
            f"result = df.groupby({_fg(xcol)})[[{_fg(y)}]].agg('{agg}').reset_index()"
        )

    # --- data_notes: top N ---
    import re as _re
    m = _re.search(r"top\s+(\d+)", notes)
    if m:
        n = int(m.group(1))
        chunks.append(f"result = result.sort_values(result.columns[-1], ascending=False).head({n})")

    return "\n".join(chunks)


def _codegen_llm(spec: ChartSpec, profile: SheetProfile, feedback: str | None = None) -> str:
    user = (
        f"Sheet schema:\n{inspect_data.inspect_text(profile)}\n\n"
        f"ChartSpec:\n{spec.model_dump_json(indent=2)}\n"
    )
    if feedback:
        user += f"\nPrevious attempt feedback (fix the code):\n{feedback}\n"
    user += "\nWrite the snippet.\n"
    try:
        out = llm_structured(AGENT_SYSTEM_PROMPT, user, CodeResult)
        return out.code
    except LLMError:
        return _codegen_deterministic(spec, _empty_df_for(profile))


def _empty_df_for(profile: SheetProfile) -> pd.DataFrame:
    return pd.DataFrame(columns=[c.name for c in profile.columns])


def _apply_edit(spec: ChartSpec, message: str, known_categories: list[str] | None = None) -> tuple[ChartSpec, str]:
    """Interpret a follow-up message as a targeted edit to a chart spec."""
    msg = message.lower().strip()

    rename_m = re.search(
        r"(?:rename|label|call)\s+(.+?)\s+(?:to|as)\s+(.+?)\s*$",
        message,
        re.IGNORECASE,
    )
    if rename_m:
        old_name = rename_m.group(1).strip().strip('"').strip("'")
        new_name = rename_m.group(2).strip().strip('"').strip("'")
        if known_categories:  # resolve the real casing from the chart's actual names
            for known in known_categories:
                if known.lower() == old_name.lower():
                    old_name = known
                    break
        if old_name and new_name and new_name.lower() != old_name.lower():
            new_spec = spec.model_copy(deep=True)
            new_spec.label_map = dict(spec.label_map or {})
            new_spec.label_map[old_name] = new_name
            return new_spec, f"Labeled '{old_name}' as '{new_name}'."
        return spec, "No change: the label was left as is."

    mapping = {
        "line chart": "line",
        "stacked bar": "bar",
        "bar chart": "bar",
        "horizontal bar": "horizontal_bar",
        "pie": "pie",
        "scatter": "scatter",
        "line": "line",
    }
    changed = None
    for phrase, ctype in mapping.items():
        if phrase in msg:
            changed = ctype
            break
    if changed and changed != spec.chart_type:
        new_spec = spec.model_copy(deep=True)
        new_spec.chart_type = changed
        return new_spec, f"Changed chart type to '{changed}'."

    if "merge" in msg and ("tail" in msg or "other" in msg):
        if spec.show_tail_categories:
            new_spec = spec.model_copy(deep=True)
            new_spec.show_tail_categories = False
            return new_spec, "Merged the long tail back into 'other' (top 10 shown)."
        return spec, "The long tail is already merged into 'other'."

    if ("label" in msg or "name" in msg) and ("group" in msg or "real" in msg):
        if not spec.show_tail_categories:
            new_spec = spec.model_copy(deep=True)
            new_spec.show_tail_categories = True
            return new_spec, "Showing every category under its real name (long-tail 'other' removed)."
        return spec, "Every category already shows its real name."

    return spec, (
        "No edit applied. I can change the chart type (e.g. 'make it a bar'), "
        "rename a label (e.g. 'rename other to group'), or show real category "
        "names ('show real names' / 'labels to group')."
    )


def resolve_edit(message: str, results: Sequence[ChartResult]) -> tuple[ChartResult | None, str]:
    """Find the ChartResult this message is about (best word overlap on titles)."""
    msg_tokens = set(re.findall(r"[a-z0-9]+", message.lower()))
    best_idx, best_score = None, 0
    for idx, r in enumerate(results):
        t = set(re.findall(r"[a-z0-9]+", r.spec.title.lower())) | {r.spec.chart_type}
        score = len(msg_tokens & t)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is None or best_score == 0:
        return None, "Could not identify which chart you mean; try mentioning its title or a column."
    return results[best_idx], "matched"


def execute_spec(spec: ChartSpec, workbook: Workbook, attempt_llm: bool = True) -> ChartResult:
    if spec.status == "skipped":
        return ChartResult(spec=spec)

    profile = workbook.profile_for(spec.sheet)
    df = workbook.frames.get(spec.sheet)
    if df is None or df.empty:
        return ChartResult(
            spec=spec,
            adaptation_note=f"Sheet '{spec.sheet}' has no data to execute.",
        )

    # --- observe -------------------------------------------------------------
    _ = inspect_data.inspect(profile)  # tool call #1: schema observation

    last_error: str | None = None
    feedback: str | None = None
    for attempt in range(MAX_ATTEMPTS):
        # --- think: write (or rewrite) the pandas snippet --------------------
        if attempt_llm:
            code = _codegen_llm(spec, profile, feedback)
        else:
            code = _codegen_deterministic(spec, df)

        # --- act: sandboxed execution ----------------------------------------
        run_result = run_code.run_snippet(df, code)  # tool call #2
        if not run_result.ok:
            last_error = run_result.error
            feedback = (
                "Security block: the code is not allowed. Rewrite it with a safe, "
                "pure-pandas approach."
            ) if run_result.blocked else (
                f"Runtime error (attempt {attempt + 1}): {run_result.error}"
            )
            if run_result.blocked:
                break  # a blocked snippet is not something to retry
            continue

        built_df = run_code.reconstruct_df(run_result)
        if built_df is None or built_df.empty or len(built_df) == 0:
            last_error = "The snippet produced an empty result."
            feedback = f"Empty result (attempt {attempt + 1}). Produce an aggregated DataFrame."
            continue

        # --- act: build the chart ---------------------------------------------
        try:
            chart = create_chart.build_chart(spec, built_df)  # tool call #3
        except ChartBuildError as exc:
            last_error = str(exc)
            feedback = f"Chart build failed (attempt {attempt + 1}): {exc}"
            continue

        # --- verify: independent closed-form recomputation against the raw frame
        verified, verification = create_chart.verify_computed(spec, df, chart.computed_summary)
        return ChartResult(
            spec=spec,
            figure_json=chart.figure_json,
            computed_summary=chart.computed_summary,
            figure_data=chart.figure_data,
            adaptation_note=chart.adaptation_note,
            verified=verified,
            verification=verification,
        )

    note = last_error or "Unknown execution error."
    return ChartResult(
        spec=spec,
        adaptation_note=f"Could not build this chart: {note}",
    )


def execute_plan(
    workbook: Workbook,
    specs: Sequence[ChartSpec],
    attempt_llm: bool = True,
    progress=None,
) -> list[ChartResult]:
    """Execute every chart spec through the tool loop.

    `progress` is an optional callable ``progress(done, total, spec)`` invoked
    before each chart for live UI feedback.
    """
    total = len(specs)
    results: list[ChartResult] = []
    for i, spec in enumerate(specs, start=1):
        if progress is not None:
            progress(i, total, spec)
        results.append(execute_spec(spec, workbook, attempt_llm=attempt_llm))
    return results


def reexecute_spec(
    workbook: Workbook,
    index: int,
    results: list[ChartResult],
    message: str,
    attempt_llm: bool = True,
) -> tuple[list[ChartResult], str]:
    """Route a follow-up chat message into the agent loop for one chart only."""
    if not (0 <= index < len(results)):
        return results, "Chart index out of range."
    target = results[index]
    if target.skipped:
        return results, "That chart was skipped during planning; no execution to edit."
    known = [str(c.get("category")) for c in (target.computed_summary or {}).get("top_categories", [])]
    key = (target.computed_summary or {}).get("grouped_by")
    if key and target.figure_data:
        for row in target.figure_data:
            v = row.get(key)
            if v is not None and not isinstance(v, bool):
                known.append(str(v))
    known = list(dict.fromkeys(known))
    spec, note = _apply_edit(target.spec, message, known_categories=known)
    new_result = execute_spec(spec, workbook, attempt_llm=attempt_llm)
    results = list(results)
    results[index] = new_result
    return results, f"{note}\nRe-executed chart: {spec.title}." if new_result.figure_json else f"{note}\nChart re-run failed: {new_result.adaptation_note}"