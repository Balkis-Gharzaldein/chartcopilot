"""Dataset data-quality checks shared by planning, execution, and verification.

Covers the failure class where source rows are misaligned: month strings
("Feb-22", "09-14-21") stored inside a categorical dimension like CUSTOMER,
or fully-blank rows. Both the sandboxed codegen (agent.py) and the
independent verifier (tools/create_chart.py) apply the SAME deterministic
rule from here, so verification never disagrees with execution about what
was excluded.
"""

from __future__ import annotations

import re

import pandas as pd

# Month-like cell values that do not belong in a non-temporal dimension:
# "Feb-22", "Jun-21", "Sep 21" and numeric dates "09-14-21", "03-03-22".
MONTH_LABEL_RE = r"(?i)^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-/. ]\d{2,4}$"
NUMERIC_DATE_RE = r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$"

# Column names that ARE temporal: month-likes belong there, never quarantined.
TEMPORAL_NAME_TOKENS = frozenset({
    "date", "time", "year", "month", "day", "quarter", "week", "period",
    "timestamp", "created", "updated", "posted",
})

# Minimum evidence before quarantining: avoids flagging a coincidental
# customer literally named like a month on tiny datasets.
MIN_QUARANTINE_COUNT = 5
MIN_QUARANTINE_SHARE = 0.01


def month_like_mask(series: pd.Series) -> pd.Series:
    """Boolean mask of month-patterned cells (NaN-safe)."""
    s = series.astype(str)
    try:
        m1 = s.str.match(MONTH_LABEL_RE, na=False)
    except Exception:
        m1 = pd.Series(False, index=series.index)
    try:
        m2 = s.str.match(NUMERIC_DATE_RE, na=False)
    except Exception:
        m2 = pd.Series(False, index=series.index)
    mask = (m1.fillna(False) | m2.fillna(False)).astype(bool)
    # Never treat genuine nulls/empties as month-like (they are blank rows).
    mask = mask & series.notna() & (s.str.strip() != "")
    return mask


def _is_temporal_named(col_name: str) -> bool:
    toks = set(re.sub(r"[^a-z0-9]+", " ", (col_name or "").lower()).split())
    return bool(toks & TEMPORAL_NAME_TOKENS)


def should_quarantine(df: pd.DataFrame, col_name: str | None) -> bool:
    """Whether month-like values must be excluded when grouping by col_name."""
    if not col_name or df is None or getattr(df, "empty", True) or col_name not in df.columns:
        return False
    if _is_temporal_named(col_name):
        return False
    try:
        non_null = df[col_name].notna() & (df[col_name].astype(str).str.strip() != "")
        n_base = int(non_null.sum())
        if n_base == 0:
            return False
        n_bad = int((month_like_mask(df[col_name]) & non_null).sum())
        return n_bad >= MIN_QUARANTINE_COUNT and (n_bad / n_base) >= MIN_QUARANTINE_SHARE
    except Exception:
        return False


def quarantine_report(df: pd.DataFrame, col_name: str | None) -> dict:
    """Count + examples of quarantined values (for adaptation notes)."""
    out = {"count": 0, "examples": []}
    if not should_quarantine(df, col_name):
        return out
    try:
        bad = df.loc[month_like_mask(df[col_name]), col_name].astype(str)
        out["count"] = int(len(bad))
        out["examples"] = sorted(bad.value_counts().head(3).index.tolist())
    except Exception:
        pass
    return out


def apply_quarantine(df: pd.DataFrame, col_name: str | None) -> pd.DataFrame:
    """Return df with month-like values of a non-temporal dimension removed."""
    if not should_quarantine(df, col_name):
        return df
    try:
        return df[~month_like_mask(df[col_name])].copy()
    except Exception:
        return df


def blank_row_count(df: pd.DataFrame | None) -> int:
    """Rows that are entirely null/empty (common in real exports)."""
    try:
        if df is None or getattr(df, "empty", True):
            return 0
        empty_str = df.apply(
            lambda s: s.astype(str).str.strip() == "" if s.dtype == object else pd.Series(False, index=s.index),
            axis=0,
        )
        return int(((df.isna() | empty_str).all(axis=1)).sum())
    except Exception:
        return 0
