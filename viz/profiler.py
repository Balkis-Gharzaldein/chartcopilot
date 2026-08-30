"""Data profiler — enriches SheetProfile with analyst-grade characteristics.

Computes per-column: role (numeric/categorical/temporal/identifier),
cardinality, cardinality_ratio, ordered, distribution hints,
and dataset-level stats. Dataset-agnostic, no domain synonyms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from schemas import SheetProfile, ColumnProfile

NUMERIC_DTYPES = {"int64", "int32", "float64", "float32", "Int64", "Float64", "int16", "float16"}

# Temporal hints from column name
TEMPORAL_TOKENS = {"date", "time", "year", "month", "day", "quarter", "week", "period", "timestamp", "created", "updated", "posted", "deadline", "found"}

ID_TOKENS = {"id", "ids", "identifier", "key", "uuid", "url", "link", "code", "token"}

DATE_REGEX = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


@dataclass
class ColumnMeta:
    name: str
    dtype: str
    role: str  # numeric | categorical | temporal | identifier
    cardinality: int
    cardinality_ratio: float
    ordered: bool
    null_rate: float
    unique_count: int
    sample_values: list[str]
    avg_label_len: float | None = None
    is_multi_valued: bool = False
    has_negatives: bool = False
    has_zeros: bool = False
    variance_zero: bool = False
    # numeric stats if applicable
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    min_val: float | None = None
    max_val: float | None = None


@dataclass
class DataProfile:
    sheet_name: str
    row_count: int
    columns: list[ColumnMeta] = field(default_factory=list)
    numeric_cols: list[str] = field(default_factory=list)
    categorical_cols: list[str] = field(default_factory=list)
    temporal_cols: list[str] = field(default_factory=list)
    identifier_cols: list[str] = field(default_factory=list)

    def by_name(self, name: str) -> ColumnMeta | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def _is_temporal_name(name: str) -> bool:
    n = name.lower().strip()
    return any(tok in n for tok in TEMPORAL_TOKENS)

def _is_temporal_samples(samples: list[str]) -> bool:
    for s in samples[:3]:
        if s and DATE_REGEX.match(str(s).strip()):
            return True
        try:
            pd.to_datetime(str(s), errors="raise")
            # heuristic: if it parses and looks like date (contains - or /)
            if any(c in str(s) for c in ["-", "/"]):
                return True
        except Exception:
            pass
    return False

def _is_identifier(name: str, cardinality_ratio: float, dtype: str) -> bool:
    n = name.lower().strip()
    if n in ID_TOKENS or n.endswith("_id") or n.endswith(" id") or n.startswith("id ") or n.startswith("id_"):
        return True
    # High cardinality alone is not identifier for numeric measures
    if dtype in NUMERIC_DTYPES:
        return False
    if cardinality_ratio > 0.95:
        return True
    return False


def profile_data(sheet: SheetProfile, df: pd.DataFrame | None = None) -> DataProfile:
    """Build enriched profile from SheetProfile + optional raw DataFrame."""
    dp = DataProfile(sheet_name=sheet.sheet_name, row_count=sheet.row_count)
    # Prepare df lookup for stats if available
    df_cols = set(df.columns) if df is not None and not df.empty else set()

    for col in sheet.columns:
        name = col.name
        dtype = col.dtype
        cardinality = col.unique_count
        ratio = (cardinality / sheet.row_count) if sheet.row_count else 0.0
        null_rate = (col.null_count / sheet.row_count) if sheet.row_count else 0.0
        samples = col.sample_values or []
        avg_len = None
        if samples:
            lens = [len(str(v)) for v in samples if v]
            avg_len = sum(lens)/len(lens) if lens else None
        is_multi = any("," in str(v) for v in samples[:5])

        # Determine role
        is_numeric_dtype = dtype in NUMERIC_DTYPES
        is_temporal = False
        if not is_numeric_dtype:
            if _is_temporal_name(name) or _is_temporal_samples(samples):
                # Check dtype also datetime
                if "datetime" in dtype.lower() or "date" in dtype.lower() or _is_temporal_samples(samples) or _is_temporal_name(name):
                    # Confirm at least one sample looks like date or name is temporal
                    if _is_temporal_samples(samples) or _is_temporal_name(name):
                        is_temporal = True
        # Identifier check before categorical (numeric never identifier)
        is_id = _is_identifier(name, ratio, dtype)

        if is_id and not is_temporal:
            role = "identifier"
        elif is_temporal:
            role = "temporal"
        elif is_numeric_dtype:
            role = "numeric"
        else:
            role = "categorical"

        # Ordered: temporal always ordered, numeric categorical not, but check if samples are sorted?
        ordered = role == "temporal"

        # Numeric stats
        mean = median = std = min_val = max_val = None
        has_neg = has_zero = var_zero = False
        if role == "numeric" and df is not None and name in df_cols:
            try:
                s = pd.to_numeric(df[name], errors="coerce").dropna()
                if len(s) > 0:
                    mean = float(s.mean())
                    median = float(s.median())
                    std = float(s.std()) if len(s) > 1 else 0.0
                    min_val = float(s.min())
                    max_val = float(s.max())
                    has_neg = bool((s < 0).any())
                    has_zero = bool((s == 0).any())
                    var_zero = bool(s.nunique() <= 1 or (std is not None and std == 0))
            except Exception:
                pass
        elif role == "numeric" and df is None:
            # without df, infer from samples if possible
            try:
                nums = [float(v) for v in samples if v not in ("", None) and _is_float(str(v))]
                if nums:
                    has_neg = any(n < 0 for n in nums)
                    has_zero = any(n == 0 for n in nums)
                    var_zero = len(set(nums)) <= 1
            except Exception:
                pass

        meta = ColumnMeta(
            name=name,
            dtype=dtype,
            role=role,
            cardinality=cardinality,
            cardinality_ratio=ratio,
            ordered=ordered,
            null_rate=null_rate,
            unique_count=cardinality,
            sample_values=samples,
            avg_label_len=avg_len,
            is_multi_valued=is_multi,
            has_negatives=has_neg,
            has_zeros=has_zero,
            variance_zero=var_zero,
            mean=mean,
            median=median,
            std=std,
            min_val=min_val,
            max_val=max_val,
        )
        dp.columns.append(meta)
        if role == "numeric":
            dp.numeric_cols.append(name)
        elif role == "categorical":
            dp.categorical_cols.append(name)
        elif role == "temporal":
            dp.temporal_cols.append(name)
        elif role == "identifier":
            dp.identifier_cols.append(name)

    return dp

def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False

def profile_workbook(profiles: list[SheetProfile], frames: dict[str, pd.DataFrame] | None = None) -> dict[str, DataProfile]:
    out: dict[str, DataProfile] = {}
    for p in profiles:
        df = frames.get(p.sheet_name) if frames else None
        out[p.sheet_name] = profile_data(p, df)
    return out
