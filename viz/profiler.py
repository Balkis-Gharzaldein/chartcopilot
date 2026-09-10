"""Data profiler — enriches SheetProfile with analyst-grade characteristics.

Computes per-column: role (numeric/categorical/temporal/identifier),
cardinality, cardinality_ratio, ordered, distribution hints,
and dataset-level stats. Dataset-agnostic, no domain synonyms.

Expanded scope (per user request):
- Data types & roles: numeric / categorical / temporal / identifier (string → categorical)
- Missing values: count + percentage (null_count, null_rate, null_pct)
- Cardinality: unique values, especially categorical
- Distribution: min/max/mean/median/std + skewness for numeric
- Outliers (IQR + z-score)
- Duplicates: duplicate rows + duplicate IDs
- Constant / near-constant columns
- Correlation between numeric variables
- Temporal coverage: min/max dates + gaps
No string pattern detection (email/URL) yet.
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
    role: str  # numeric | categorical | temporal | identifier (string → categorical)
    cardinality: int
    cardinality_ratio: float
    ordered: bool
    null_rate: float
    null_count: int
    null_pct: float  # 0-100
    unique_count: int
    sample_values: list[str]
    avg_label_len: float | None = None
    is_multi_valued: bool = False
    has_negatives: bool = False
    has_zeros: bool = False
    variance_zero: bool = False
    is_constant: bool = False
    is_near_constant: bool = False  # >95% same value or cardinality==1 with >98% frequency
    # numeric stats if applicable
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    min_val: float | None = None
    max_val: float | None = None
    skewness: float | None = None
    # outlier summary for numeric
    outlier_count: int | None = None
    outlier_pct: float | None = None
    outlier_method: str | None = None  # IQR or zscore
    # temporal coverage if applicable
    temporal_min: str | None = None  # ISO date string
    temporal_max: str | None = None
    temporal_gaps: int | None = None  # number of missing periods / gaps detected
    temporal_coverage_days: int | None = None
    temporal_missing_ranges: list[list[str]] | None = None  # [[start_iso, end_iso], ...] gaps enumerated (truncated to 5)
    # memory footprint per column
    memory_bytes: int | None = None
    memory_kb: float | None = None
    memory_mb: float | None = None
    # categorical summary: top 3 values by frequency
    top_values: list[dict] | None = None  # [{value, count, pct}]


@dataclass
class DataProfile:
    sheet_name: str
    row_count: int
    columns: list[ColumnMeta] = field(default_factory=list)
    numeric_cols: list[str] = field(default_factory=list)
    categorical_cols: list[str] = field(default_factory=list)
    temporal_cols: list[str] = field(default_factory=list)
    identifier_cols: list[str] = field(default_factory=list)
    # dataset-level
    duplicate_rows: int = 0
    duplicate_pct: float = 0.0
    duplicate_ids: dict[str, int] = field(default_factory=dict)  # col -> duplicate count
    constant_columns: list[str] = field(default_factory=list)
    near_constant_columns: list[str] = field(default_factory=list)
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)  # numeric col -> {other: corr}
    top_correlations: list[tuple[str, str, float]] = field(default_factory=list)  # sorted |corr| desc
    total_memory_bytes: int = 0
    total_memory_kb: float = 0.0
    total_memory_mb: float = 0.0

    def by_name(self, name: str) -> ColumnMeta | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def _is_temporal_name(name: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", name.lower())) & TEMPORAL_TOKENS)

def _is_temporal_samples(samples: list[str]) -> bool:
    import warnings

    for s in samples[:3]:
        if s and DATE_REGEX.match(str(s).strip()):
            return True
        try:
            with warnings.catch_warnings():
                # format="mixed" parses each element individually without the
                # "Could not infer format" UserWarning that buried real logs.
                warnings.simplefilter("ignore", UserWarning)
                try:
                    pd.to_datetime(str(s), errors="raise", format="mixed")
                except TypeError:
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
    # Bare row-id columns ("index", "idx") of integers are identifiers even
    # though their dtype is numeric — otherwise they pollute every numeric
    # fallback (first-numeric defaults, heatmaps) with meaningless row ids.
    # Gated on integer dtype so a genuine measure never matches by accident.
    if n in {"index", "idx", "row_number", "rownumber", "level_0"} and dtype.lower().startswith("int"):
        return True
    if n.startswith("unnamed:") and cardinality_ratio > 0.95 and dtype.lower().startswith("int"):
        return True
    # High cardinality alone is not identifier for numeric measures
    if dtype in NUMERIC_DTYPES:
        return False
    if cardinality_ratio > 0.95:
        return True
    return False


def _compute_skewness(s: pd.Series) -> float | None:
    try:
        if len(s) < 3:
            return None
        return float(s.skew())
    except Exception:
        return None


def _compute_outliers(s: pd.Series) -> tuple[int, float, str]:
    """Return (count, pct 0-100, method) using IQR primary, z-score fallback."""
    try:
        if len(s) < 5:
            return 0, 0.0, "IQR"
        q1 = s.quantile(0.25)
        q3 = s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0 or pd.isna(iqr):
            # Fallback to z-score >3
            std = s.std()
            if std and std != 0 and not pd.isna(std):
                z = (s - s.mean()).abs() / std
                cnt = int((z > 3).sum())
                return cnt, float(cnt / len(s) * 100) if len(s) else 0.0, "zscore"
            return 0, 0.0, "IQR"
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        cnt = int(((s < lower) | (s > upper)).sum())
        return cnt, float(cnt / len(s) * 100) if len(s) else 0.0, "IQR"
    except Exception:
        return 0, 0.0, "IQR"


def _compute_temporal_coverage(df: pd.DataFrame, col_name: str) -> tuple[str | None, str | None, int | None, int | None, list[list[str]] | None]:
    """Return (min_iso, max_iso, gaps, coverage_days, missing_ranges) for a temporal column."""
    try:
        if col_name not in df.columns:
            return None, None, None, None, None
        try:
            ser = pd.to_datetime(df[col_name], errors="coerce", format="mixed").dropna()
        except TypeError:
            ser = pd.to_datetime(df[col_name], errors="coerce").dropna()
        if ser.empty:
            return None, None, None, None, None
        ser = ser.drop_duplicates().sort_values()
        min_iso = ser.iloc[0].isoformat()
        max_iso = ser.iloc[-1].isoformat()
        try:
            coverage_days = int((ser.iloc[-1] - ser.iloc[0]).days)
        except Exception:
            coverage_days = None
        gaps: int | None = None
        missing_ranges: list[list[str]] | None = None
        try:
            diffs = ser.diff().dropna()
            if len(diffs) >= 2:
                median_diff = diffs.median()
                if pd.notna(median_diff) and median_diff.total_seconds() > 0:
                    gaps = int((diffs > median_diff * 1.5).sum())
                    # Enumerate missing_ranges where gaps occur (truncated to 5)
                    if gaps and gaps > 0:
                        missing_ranges = []
                        # Use unique sorted timestamps to find gaps
                        uniq = ser.drop_duplicates().sort_values()
                        for i in range(1, len(uniq)):
                            diff = uniq.iloc[i] - uniq.iloc[i - 1]
                            if diff > median_diff * 1.5:
                                # gap from prev + median to curr - median (approx missing interval)
                                start_gap = (uniq.iloc[i - 1] + median_diff).isoformat()
                                end_gap = (uniq.iloc[i] - median_diff).isoformat()
                                # fallback to string gap boundaries
                                if start_gap and end_gap:
                                    missing_ranges.append([start_gap[:10], end_gap[:10]])
                                    if len(missing_ranges) >= 5:
                                        break
                                else:
                                    missing_ranges.append([uniq.iloc[i - 1].isoformat()[:10], uniq.iloc[i].isoformat()[:10]])
                        if not missing_ranges:
                            missing_ranges = None
                else:
                    gaps = 0
            else:
                gaps = 0
        except Exception:
            gaps = None
        return min_iso, max_iso, gaps, coverage_days, missing_ranges
    except Exception:
        return None, None, None, None, None


def profile_data(sheet: SheetProfile, df: pd.DataFrame | None = None) -> DataProfile:
    """Build enriched profile from SheetProfile + optional raw DataFrame.

    Keeps string mapped to categorical (no separate string role) per user request.
    Expanded to cover: missing % , cardinality, skewness, outliers, constant/near-constant,
    duplicates, correlation, temporal gaps.
    """
    dp = DataProfile(sheet_name=sheet.sheet_name, row_count=sheet.row_count)
    # Prepare df lookup for stats if available
    df_cols = set(df.columns) if df is not None and not df.empty else set()

    for col in sheet.columns:
        name = col.name
        dtype = col.dtype
        cardinality = col.unique_count
        ratio = (cardinality / sheet.row_count) if sheet.row_count else 0.0
        null_count = int(col.null_count)
        null_rate = (null_count / sheet.row_count) if sheet.row_count else 0.0
        null_pct = float(null_rate * 100)
        samples = col.sample_values or []
        avg_len = None
        if samples:
            lens = [len(str(v)) for v in samples if v]
            avg_len = sum(lens)/len(lens) if lens else None
        is_multi = any("," in str(v) for v in samples[:5])

        # Determine role (string → categorical) — also detect numeric via samples for cases like GROSS AMT with dtype object
        is_numeric_dtype = dtype in NUMERIC_DTYPES
        # Check if samples look numeric after stripping $, commas, %, spaces
        is_numeric_samples = False
        if not is_numeric_dtype and samples:
            try:
                cleaned = [str(v).replace("$","").replace(",","").replace("%","").strip() for v in samples[:5] if v not in ("", None)]
                if cleaned and all(_is_float(c) or _is_float(c.replace(" ","")) for c in cleaned if c):
                    # At least 60% parse as float
                    parseable = sum(1 for c in cleaned if _is_float(c))
                    if parseable >= len(cleaned) * 0.6:
                        is_numeric_samples = True
            except Exception:
                pass
        is_temporal = False
        if not is_numeric_dtype and not is_numeric_samples:
            if _is_temporal_name(name) or _is_temporal_samples(samples):
                if "datetime" in dtype.lower() or "date" in dtype.lower() or _is_temporal_samples(samples) or _is_temporal_name(name):
                    if _is_temporal_samples(samples) or _is_temporal_name(name):
                        is_temporal = True
        # A name is a hint, not evidence that arbitrary text is a date.
        is_temporal = "datetime" in dtype.lower() or _is_temporal_samples(samples)
        if df is not None and name in df_cols and is_temporal:
            values = df[name].dropna()
            parsed = pd.to_datetime(values, errors="coerce", format="mixed")
            is_temporal = bool(len(values) and parsed.notna().mean() >= 0.9)
        is_id = _is_identifier(name, ratio, "float64" if is_numeric_samples else dtype)

        if is_id and not is_temporal:
            role = "identifier"
        elif is_temporal:
            role = "temporal"
        elif is_numeric_dtype or is_numeric_samples:
            role = "numeric"
        else:
            role = "categorical"

        ordered = role == "temporal"

        # Numeric stats + skewness/outliers/constant
        mean = median = std = min_val = max_val = skewness = None
        outlier_count = outlier_pct = None
        outlier_method = None
        is_constant = False
        is_near_constant = False
        has_neg = has_zero = var_zero = False
        temporal_min = temporal_max = None
        temporal_gaps = temporal_coverage_days = None
        temporal_missing_ranges = None
        memory_bytes = memory_kb = memory_mb = None
        top_values = None

        if role == "numeric":
            is_multi = False  # Thousands separators are not multi-label fields.
        if role == "numeric" and df is not None and name in df_cols:
            try:
                # Late cleaning for currency strings — preserve raw but ensure numeric for stats
                s_raw = df[name].astype(str).str.replace(r'[\$,%]', '', regex=True).str.strip()
                s_raw = s_raw.replace("", pd.NA)
                s = pd.to_numeric(s_raw, errors="coerce").dropna()
                if len(s) > 0:
                    mean = float(s.mean())
                    median = float(s.median())
                    std = float(s.std()) if len(s) > 1 else 0.0
                    min_val = float(s.min())
                    max_val = float(s.max())
                    skewness = _compute_skewness(s)
                    outlier_count, outlier_pct, outlier_method = _compute_outliers(s)
                    has_neg = bool((s < 0).any())
                    has_zero = bool((s == 0).any())
                    var_zero = bool(s.nunique() <= 1 or (std is not None and std == 0))
                    is_constant = bool(s.nunique() <= 1)
                    # near-constant: >95% same value or cardinality 1 with high frequency, or 98% top freq
                    if not is_constant and len(s) > 0:
                        top_freq = s.value_counts(normalize=True).iloc[0] if len(s.value_counts()) > 0 else 0
                        is_near_constant = bool(top_freq >= 0.95 or (s.nunique() == 2 and top_freq >= 0.98))
                    # also flag constant from SheetProfile ratio
                    if cardinality == 1:
                        is_constant = True
                else:
                    var_zero = True
                    is_constant = True
            except Exception:
                pass
        elif role == "numeric" and df is None:
            try:
                nums = [float(v) for v in samples if v not in ("", None) and _is_float(str(v))]
                if nums:
                    has_neg = any(n < 0 for n in nums)
                    has_zero = any(n == 0 for n in nums)
                    var_zero = len(set(nums)) <= 1
                    is_constant = var_zero
            except Exception:
                pass

        # Per-column constant flags also for categorical/temporal/identifier (cardinality based)
        if role != "numeric":
            if cardinality == 1 and sheet.row_count > 0:
                is_constant = True
            elif cardinality > 1 and sheet.row_count > 0:
                # near-constant heuristic without df: unique ratio very low or sample dominated
                # With df we can check top frequency
                if df is not None and name in df_cols:
                    try:
                        ser = df[name].dropna()
                        if len(ser) > 0:
                            top_freq = ser.value_counts(normalize=True).iloc[0]
                            is_near_constant = bool(top_freq >= 0.95)
                            if ser.nunique() <= 1:
                                is_constant = True
                    except Exception:
                        pass

        # Memory footprint per column (deep)
        if df is not None and name in df_cols:
            try:
                # deep includes object strings; for sampled large df still accurate
                mem = int(df[name].memory_usage(deep=True))
                memory_bytes = mem
                memory_kb = round(mem / 1024, 1)
                memory_mb = round(mem / (1024 * 1024), 2)
            except Exception:
                pass

        # Categorical summary: top 3 values with count + pct (bar-friendly)
        if role == "categorical" and df is not None and name in df_cols:
            try:
                ser = df[name].dropna()
                if len(ser) > 0:
                    vc = ser.value_counts(dropna=False).head(3)
                    total = len(ser)
                    top_values = [
                        {"value": str(v), "count": int(c), "pct": round(float(c / total * 100), 1)}
                        for v, c in vc.items()
                    ]
            except Exception:
                pass

        # Temporal coverage
        if role == "temporal" and df is not None and name in df_cols:
            temporal_min, temporal_max, temporal_gaps, temporal_coverage_days, temporal_missing_ranges = _compute_temporal_coverage(df, name)

        meta = ColumnMeta(
            name=name,
            dtype=dtype,
            role=role,
            cardinality=cardinality,
            cardinality_ratio=ratio,
            ordered=ordered,
            null_rate=null_rate,
            null_count=null_count,
            null_pct=round(null_pct, 2),
            unique_count=cardinality,
            sample_values=samples,
            avg_label_len=avg_len,
            is_multi_valued=is_multi,
            has_negatives=has_neg,
            has_zeros=has_zero,
            variance_zero=var_zero,
            is_constant=is_constant,
            is_near_constant=is_near_constant,
            mean=mean,
            median=median,
            std=std,
            min_val=min_val,
            max_val=max_val,
            skewness=round(skewness, 3) if skewness is not None else None,
            outlier_count=outlier_count,
            outlier_pct=round(outlier_pct, 2) if outlier_pct is not None else None,
            outlier_method=outlier_method,
            temporal_min=temporal_min,
            temporal_max=temporal_max,
            temporal_gaps=temporal_gaps,
            temporal_coverage_days=temporal_coverage_days,
            temporal_missing_ranges=temporal_missing_ranges,
            memory_bytes=memory_bytes,
            memory_kb=memory_kb,
            memory_mb=memory_mb,
            top_values=top_values,
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

    # --- dataset-level calculations ---
    # Memory footprint total + duplicates + constants + correlation
    if df is not None and not df.empty:
        # Total memory footprint (deep) — both MB + KB for display
        try:
            total_bytes = int(df.memory_usage(deep=True).sum())
            dp.total_memory_bytes = total_bytes
            dp.total_memory_kb = round(total_bytes / 1024, 1)
            dp.total_memory_mb = round(total_bytes / (1024 * 1024), 2)
        except Exception:
            pass
        try:
            dup_rows = int(df.duplicated(keep="first").sum())
            dp.duplicate_rows = dup_rows
            dp.duplicate_pct = round(float(dup_rows / len(df) * 100), 2) if len(df) else 0.0
        except Exception:
            pass
        # Duplicate IDs per identifier column
        for id_col in dp.identifier_cols:
            if id_col in df.columns:
                try:
                    ser = df[id_col].dropna()
                    dup_id = int(ser.duplicated(keep="first").sum())
                    if dup_id > 0:
                        dp.duplicate_ids[id_col] = dup_id
                except Exception:
                    pass
        # Constant / near-constant lists
        dp.constant_columns = [c.name for c in dp.columns if c.is_constant]
        dp.near_constant_columns = [c.name for c in dp.columns if c.is_near_constant and not c.is_constant]
        # Correlation between numeric variables — clean currency strings late
        if len(dp.numeric_cols) >= 2:
            try:
                def _clean_num(s):
                    return pd.to_numeric(s.astype(str).str.replace(r'[\$,%]', '', regex=True).str.strip(), errors="coerce")
                numeric_df = pd.DataFrame({col: _clean_num(df[col]) for col in dp.numeric_cols})
                corr = numeric_df.corr(numeric_only=True, min_periods=5)
                # Store matrix as dict
                mat: dict[str, dict[str, float]] = {}
                for col in corr.columns:
                    mat[col] = {}
                    for other in corr.columns:
                        val = corr.loc[col, other]
                        if pd.notna(val):
                            mat[col][other] = round(float(val), 3)
                dp.correlation_matrix = mat
                # Top correlations (unique pairs, |corr| desc, exclude self, threshold 0.3)
                pairs: list[tuple[str, str, float]] = []
                for i, c1 in enumerate(corr.columns):
                    for c2 in corr.columns[i+1:]:
                        v = corr.loc[c1, c2]
                        if pd.notna(v):
                            a, b = sorted((str(c1), str(c2)))
                            pairs.append((a, b, round(float(v), 3)))
                pairs.sort(key=lambda x: (-abs(x[2]), x[0], x[1]))
                dp.top_correlations = pairs[:5]
            except Exception:
                pass

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
