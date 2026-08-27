"""Narrative synthesis -- LLM call #2.

Input: ONLY the computed_summary dict from each executed ChartResult (aggregates,
never raw rows, never the uploaded file).  Every sentence must trace back to one
of those numbers.  Falls back to deterministic template sentences when no API key
is configured.
"""

from __future__ import annotations

from typing import Sequence

from llm import LLMError, llm_text
from schemas import ChartResult

NARRATIVE_SYSTEM_PROMPT = (
    "You write a short plain-language summary of data visualizations (150-300 words). "
    "Every claim you make must be directly supported by a number in the provided "
    "summaries. Do not introduce any figure, trend, or comparison that isn't present "
    "in the input data. Cite each figure inline in parentheses next to the claim it "
    "supports, naming the measure, e.g. (total sales: 790,506) or (top: Widget Pulse, "
    "32,187)."
)

WORD_LIMIT = 300


def _fallback_sentence(desc, r: ChartResult) -> str:
    s = r.computed_summary or {}
    title = (desc or r.spec.title or "chart").strip()[:120]
    measure = s.get("measure")
    grouped_by = s.get("grouped_by")
    cite = (
        f" {measure} across {grouped_by}" if measure and grouped_by and measure != grouped_by
        else f" {measure}" if measure else ""
    )
    if not s:
        return f"No figures were computed for '{title}'."
    top = (s.get("top_categories") or [{}])[0]
    if "total" in s and top:
        share = s.get("top_share")
        share_txt = f" ({share:.0%} of the total)" if share else ""
        return (
            f"The top group in '{title}' is '{top.get('category')}' at "
            f"{top.get('value'):g} of {s.get('total'):g} total{cite}{share_txt}, "
            f"across {s.get('n_categories', 0)} groups shown."
        )
    if s.get("chart_type") == "line":
        change = s.get("change_pct")
        change_txt = f"a {change:g}% change" if change is not None else "a stable reading"
        return (
            f"'{title}' shows the metric{cite} moving from {s.get('first_value'):g} to "
            f"{s.get('last_value'):g} over {s.get('points', 0)} points ({change_txt})."
        )
    if s.get("chart_type") == "scatter":
        corr = s.get("correlation")
        corr_txt = f"a correlation of {corr:g}" if corr is not None else "no meaningful correlation"
        return (
            f"'{title}' compares {s.get('points', 0)} points with {corr_txt} "
            f"(x: {s.get('x_min'):g}-{s.get('x_max'):g}, y: {s.get('y_min'):g}-{s.get('y_max'):g})."
        )
    return f"'{title}' summarizes an aggregate of {s.get('total', 0):g}{cite}."


def _deterministic_narrative(results: Sequence[ChartResult]) -> str:
    sentences = []
    for r in results:
        if r.skipped:
            continue
        if not r.computed_summary:
            continue
        sentences.append(_fallback_sentence(None, r))
    weights = len(sentences)
    text = " ".join(sentences)
    if len(text.split()) > WORD_LIMIT:
        text = " ".join(text.split()[:WORD_LIMIT])
    if weights == 0:
        return "No charts could be produced to summarize."
    return text


def _build_user_prompt(results: Sequence[ChartResult]) -> str:
    blocks = []
    for r in results:
        if r.skipped or not r.computed_summary:
            continue
        label = r.adaptation_note or "no adaptation"
        blocks.append(
            f"Chart: {r.spec.title!r} (type={r.spec.chart_type}, agg={r.spec.agg_function}, "
            f"x={r.spec.x}, y={r.spec.y}, adaptation={label!r})\n"
            f"Computed summary: {r.computed_summary}"
        )
    if not blocks:
        return "No computed summaries to summarize."
    return "\n\n".join(blocks)


def synthesize_narrative(results: Sequence[ChartResult]) -> str:
    """Synthesize a grounded plain-language summary from computed aggregates."""
    user = _build_user_prompt(results)
    try:
        text = llm_text(NARRATIVE_SYSTEM_PROMPT, user, max_tokens=600)
    except LLMError:
        text = _deterministic_narrative(results)
    text = (text or "").strip()
    if len(text.split()) > WORD_LIMIT:
        text = " ".join(text.split()[:WORD_LIMIT])
    return text or "No charts could be produced to summarize."