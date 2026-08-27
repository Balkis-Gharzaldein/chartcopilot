"""Guideline extraction.

A guideline is a list of plain text lines, one chart request per line.  It comes
from either a sheet whose name fuzzy-matches "instructions"/"guideline" or, when
no such sheet exists, from a freeform text box in the UI.
"""

from __future__ import annotations

from schemas import GuidelineLines

INSTRUCTIONS_KEYWORDS = ("instructions", "guideline")


def find_instructions_sheet(sheet_names: list[str]) -> str | None:
    """Return the name of a sheet whose name fuzzy-matches the instructions
    sheet convention (case-insensitive substring match on "instructions" or
    "guideline", tolerating underscores/spaces)."""
    norm = [s.strip().lower().replace("_", " ").replace("-", " ") for s in sheet_names]
    for idx, n in enumerate(norm):
        if any(kw in n for kw in INSTRUCTIONS_KEYWORDS):
            return sheet_names[idx]
    return None


def _cells_to_lines(ws) -> list[str]:
    """Read every cell of the Instructions sheet into guideline lines.

    Each non-empty cell becomes one line; long cells spanning multiple lines are
    split on newlines. Duplicate/blank lines are dropped.
    """
    lines: list[str] = []
    seen = set()
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if cell is None:
                continue
            for part in str(cell).replace("\r", "\n").split("\n"):
                part = part.strip()
                if part and part.lower() not in seen:
                    seen.add(part.lower())
                    lines.append(part)
    return lines


def extract_guideline(workbook, text_area: str = "") -> GuidelineLines:
    """Extract guideline lines from the workbook.

    Instructions-sheet lines take precedence; text-area lines are appended when a
    sheet is present (to supplement it) or used alone when there is no sheet.

    Args:
        workbook: the ingestion Workbook (profiles + frames + raw openpyxl book).
        text_area: freeform guideline text from the UI.
    """
    sheet_names = [p.sheet_name for p in workbook.profiles]
    inst_sheet = find_instructions_sheet(sheet_names)

    lines: list[str] = []
    if inst_sheet is not None and workbook.raw is not None:
        lines = _cells_to_lines(workbook.raw[inst_sheet])

    if text_area and text_area.strip():
        extra = [ln.strip() for ln in text_area.replace("\r", "\n").split("\n") if ln.strip()]
        for ln in extra:
            if ln not in lines:
                lines.append(ln)

    if not lines:
        return GuidelineLines(lines=[], source="text_area")

    source = "instructions_sheet" if (inst_sheet is not None and lines) else "text_area"
    return GuidelineLines(
        lines=lines,
        source=source,
        instructions_sheet=inst_sheet if source == "instructions_sheet" else None,
    )