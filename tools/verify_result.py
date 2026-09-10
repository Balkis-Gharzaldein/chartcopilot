"""Independent, host-side verification of complete aggregated result tables."""

from __future__ import annotations

import re

import pandas as pd

from schemas import ChartSpec
from tools.data_quality import apply_quarantine


def verify_aggregates(spec: ChartSpec, source: pd.DataFrame, actual: pd.DataFrame):
    """Return a full-table check; None means this transformation is unsupported.

    This intentionally does not execute generated code. Expected groups and
    values are calculated directly from source records through a separate path.
    """
    from tools.create_chart import (
        _apply_notes_filters_for_verify, _apply_topn_for_verify,
        _clean_numeric_verify, _with_time_bucket_col,
    )

    notes = (spec.data_notes or "").lower()
    if "derived metric:" in notes or "last " in notes:
        return None
    data = source.copy()
    # Simple comparison filters are independently applied before aggregation.
    if spec.filter and "in_top_n" not in spec.filter:
        match = re.fullmatch(r"\s*(.*?)\s*(==|!=|>=|<=|>|<|=)\s*(.*?)\s*", spec.filter)
        if not match:
            return None
        col, op, value = match.groups()
        col = next((c for c in data if c.lower() == col.lower()), col)
        if col not in data:
            return None
        value = value.strip("\"'")
        if pd.api.types.is_numeric_dtype(data[col]):
            try:
                value = float(value)
            except ValueError:
                return None
        operations = {"=": "eq", "==": "eq", "!=": "ne", ">": "gt", "<": "lt", ">=": "ge", "<=": "le"}
        data = data[getattr(data[col], operations[op])(value)]
    data = _apply_notes_filters_for_verify(data, spec)
    data = apply_quarantine(data, spec.x)
    data = _apply_topn_for_verify(data, spec)
    data, x = _with_time_bucket_col(data, spec, spec.x)
    if x not in data:
        return None
    if any(term in notes for term in ("split", "comma", "explode")):
        data = data[data[x].notna()].copy()
        data[x] = data[x].astype(str).str.split(",")
        data = data.explode(x)
        data[x] = data[x].str.strip()
        data = data[data[x] != ""]
    keys = [x] + ([spec.group_by] if spec.group_by and spec.group_by not in (x, spec.y) else [])
    agg = spec.agg_function or "sum"
    if agg == "count":
        expected = data.groupby(keys).size().reset_index(name="count")
        value_col = "count"
    elif agg == "count_distinct":
        if spec.y not in data:
            return None
        expected = data.groupby(keys)[spec.y].nunique().reset_index(name="count")
        value_col = "count"
    elif agg in ("sum", "mean", "median", "min", "max") and spec.y in data:
        data[spec.y] = _clean_numeric_verify(data[spec.y])
        expected = data.groupby(keys)[spec.y].agg(agg).reset_index()
        value_col = spec.y
    else:
        return None
    top = re.search(r"top\s+(\d+)", notes)
    if top and spec.chart_type not in ("line", "area") and not (spec.group_by and re.search(r"top\s+\d+\s+by\b", notes)):
        expected = expected.sort_values(value_col, ascending=False).head(int(top.group(1)))
    try:
        cols = keys + [value_col]
        left = expected[cols].copy()
        right = actual[cols].copy()
        for key in keys:
            # Sandbox transport serializes timestamps as ISO strings.
            if pd.api.types.is_datetime64_any_dtype(left[key]):
                right[key] = pd.to_datetime(right[key], errors="coerce", format="mixed")
        left = left.sort_values(keys).reset_index(drop=True)
        right = right.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-9, atol=1e-8)
    except (AssertionError, KeyError, ValueError) as exc:
        return False, {"scope": "all_groups", "checks": {"all_groups": {"ok": False, "expected_rows": len(expected), "found_rows": len(actual)}}, "failed": ["all_groups"], "error": str(exc)}
    return True, {"scope": "all_groups", "checks": {"all_groups": {"ok": True, "expected_rows": len(expected), "found_rows": len(actual)}}, "failed": []}
