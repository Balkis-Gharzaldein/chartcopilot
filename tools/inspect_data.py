"""inspect_data tool: schema + sample summary for a sheet (never raw data)."""

from __future__ import annotations

from schemas import SheetProfile


def inspect(profile: SheetProfile) -> dict:
    """Return a human/LLM-readable description of a sheet's structure.

    Deliberately exposes only the profile (column names, dtypes, small samples,
    null/unique counts) -- full rows never leave memory.
    """
    columns = []
    for c in profile.columns:
        columns.append(
            {
                "name": c.name,
                "dtype": c.dtype,
                "null_count": c.null_count,
                "unique_count": c.unique_count,
                "sample_values": c.sample_values[:5],
            }
        )
    return {
        "sheet": profile.sheet_name,
        "row_count": profile.row_count,
        "columns": columns,
    }


def inspect_text(profile: SheetProfile) -> str:
    d = inspect(profile)
    lines = [f"Sheet '{d['sheet']}': {d['row_count']} rows"]
    for c in d["columns"]:
        samples = ", ".join(map(str, c["sample_values"])) or "n/a"
        lines.append(
            f"- {c['name']} ({c['dtype']}, {c['null_count']} null, "
            f"{c['unique_count']} unique | samples: {samples})"
        )
    return "\n".join(lines)