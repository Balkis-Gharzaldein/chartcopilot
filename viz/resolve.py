"""Single column resolver — one source of truth for NL -> actual column.

Replaces the 4 fragmented matchers:
- planning.py _column_score / _synonym_score
- viz/candidates.py _col_score
- viz/orchestrator.py _validate_vizspec._find_closest
- planning.py _find_explicit_columns

Design:
- normalize: lower, `_`/`-` -> space, `amt` token -> `amount`, strip $,%,
  collapse whitespace. Fixes the phrase-vs-token bug where
  {"gross","amt"} never intersected {"gross amt"}.
- scoring: exact (10) > substring (3) + token overlap (1.2 each)
  + alias-concept overlap (2.0 each) + role bias (+0.5 / -1.0).
- threshold: score >= 1.0 required, else None (caller must skip with
  Did-you-mean instead of silently picking num_cols[0]).
"""

from __future__ import annotations

import re

_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """Lower, underscores/dashes -> space, amt token -> amount."""
    s = _NORM_RE.sub(" ", (name or "").lower()).strip()
    s = re.sub(r"\bamt\b", "amount", s)
    # Treat common dataset naming variants as the same analytical concept.
    s = re.sub(r"\bfulfilment\b|\bfulfilled\b", "fulfillment", s)
    s = re.sub(r"\bcities\b", "city", s)
    s = re.sub(r"\bstates\b", "state", s)
    s = re.sub(r"\bregions\b", "region", s)
    s = re.sub(r"\bcountries\b", "country", s)
    s = re.sub(r"\bstyles\b", "style", s)
    s = re.sub(r"\bcategories\b", "category", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokens(name: str) -> set[str]:
    return set(normalize(name).split()) - {""}


# Curated alias concepts. Scoped narrowly — notably `style` is NOT a
# synonym for `region` (that poison entry caused DATE/Style confusion).
# Each concept maps to the set of normalized phrases/tokens that imply it.
CONCEPTS: dict[str, set[str]] = {
    "revenue": {
        "revenue", "revenues", "sales", "sale", "amount", "value", "gross amount",
        "order amount", "order value", "sales amount", "total sales", "turnover", "receipts",
        "gross",  # token-level: GROSS AMT matches via amount+gross
    },
    "customer": {
        "customer", "customers", "client", "clients", "buyer", "buyers",
        "account", "accounts",
    },
    "product": {
        "product", "products", "item", "items", "sku", "style", "styles",
        "category", "categories", "variant", "variants",
    },
    "date": {
        "date", "dates", "time", "month", "months", "year", "years",
        "day", "days", "quarter", "quarters", "period", "timestamp",
    },
    "quantity": {
        "qty", "quantity", "pcs", "units", "volume", "count",
    },
    "price": {
        "price", "prices", "rate", "rates", "cost", "costs",
    },
    "size": {
        "size", "sizes",
    },
    "location": {
        "city", "cities", "state", "states", "country", "countries",
        "region", "regions", "location", "locations", "province", "district",
    },
    "fulfillment": {
        "fulfilment", "fulfillment", "fulfilled", "shipping", "delivery", "dispatch",
    },
    "courier": {"courier", "carrier", "tracking", "shipment status"},
}


def _concepts_of_toks(toks: set[str]) -> set[str]:
    found: set[str] = set()
    for concept, members in CONCEPTS.items():
        member_toks: set[str] = set()
        for m in members:
            member_toks.update(m.split())
        if toks & member_toks:
            found.add(concept)
    return found


def _concepts_of_phrase(norm_phrase: str) -> set[str]:
    found: set[str] = set()
    for concept, members in CONCEPTS.items():
        if norm_phrase in members:
            found.add(concept)
    return found


def score_candidate(query: str, col: str) -> float:
    """Raw match score without role bias. Used by tests and callers."""
    qnorm = normalize(query)
    cnorm = normalize(col)
    if not qnorm or not cnorm:
        return 0.0
    if qnorm == cnorm:
        return 10.0
    score = 0.0
    if qnorm in cnorm or cnorm in qnorm:
        # require meaningful length to avoid "a" in "date"
        if len(qnorm) >= 3 and len(cnorm) >= 2:
            score += 3.0
    qtoks = set(qnorm.split())
    ctoks = set(cnorm.split())
    score += 1.2 * len(qtoks & ctoks)
    # alias-concept overlap at token level (fixes gross amt vs revenue)
    score += 2.0 * len(_concepts_of_toks(qtoks) & _concepts_of_toks(ctoks))
    # alias-concept overlap at whole-phrase level (revenue == sales concept)
    score += 2.0 * len(_concepts_of_phrase(qnorm) & _concepts_of_phrase(cnorm))
    if not (qtoks & ctoks) and not (
        _concepts_of_toks(qtoks) & _concepts_of_toks(ctoks)
    ) and not (_concepts_of_phrase(qnorm) & _concepts_of_phrase(cnorm)):
        # no shared signal at all — avoid substring-only false positives
        # e.g. "age" in "average" style matches; keep exact/substring bonus
        # only when there is also token or concept evidence, except the
        # qnorm-in-cnorm branch already added above for real substrings like
        # "customer" in "customers". If score is only 3.0 from a weak
        # substring (len<4 overlap), drop it.
        if score == 3.0 and len(qtoks) == 1 and len(next(iter(qtoks))) <= 3:
            return 0.0
    return score


def score_query(query: str, col: str) -> float:
    """Score a full NL question against one column: whole-phrase score plus
    the best single-word score.

    Whole-phrase matching alone ties when a question names two measures
    ("…by revenue, and how many units…": GROSS AMT and PCS both ~2.0), while
    per-word max separates them ("revenue"→GROSS AMT 4.0, "units"→PCS 2.0).
    """
    if not query or not col:
        return 0.0
    best = score_candidate(query, col)
    for w in normalize(query).split():
        if len(w) < 3:
            continue
        s = score_candidate(w, col)
        if s > best:
            best = s
    return best


def resolve_column(
    query: str,
    columns: list[str],
    role_map: dict[str, str] | None = None,
    prefer_role: str | None = None,
    threshold: float = 1.0,
) -> tuple[str | None, float]:
    """Resolve NL query to exact column name. Returns (col|None, score)."""
    if not query or not columns:
        return None, 0.0
    qnorm = normalize(query)
    # 1. exact normalized match wins immediately
    for col in columns:
        if normalize(col) == qnorm:
            return col, 10.0
    best: str | None = None
    best_score = 0.0
    role_map = role_map or {}
    for col in columns:
        s = score_candidate(query, col)
        role = role_map.get(col)
        if prefer_role and role:
            if role == prefer_role:
                s += 0.5
            else:
                # penalize role mismatch but don't hard-filter (fallback
                # without role filter happens naturally via lower score)
                if prefer_role == "numeric" and role in ("categorical", "identifier"):
                    s -= 1.0
                elif prefer_role == "categorical" and role in ("numeric",):
                    s -= 1.0
                elif prefer_role == "temporal" and role not in ("temporal",):
                    s -= 1.0
        if s > best_score:
            best, best_score = col, s
    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score


def suggest_columns(
    query: str,
    columns: list[str],
    role_map: dict[str, str] | None = None,
    k: int = 3,
) -> list[str]:
    """Top-k closest columns for Did-you-mean messages."""
    scored = [(score_candidate(query, c), c) for c in columns]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:k] if s > 0][:k]


def looks_numeric_samples(samples: list[str] | tuple[str, ...] | None) -> bool:
    """True when string samples parse as numbers after stripping $, commas, %.

    Same rule as the profiler: currency/text-stored measures (e.g. "$1,200",
    "45%") count as numeric. Used by the deterministic planner so
    object-dtype measures are not invisible to measure lookup.
    """
    if not samples:
        return False
    cleaned = [
        str(v).replace("$", "").replace(",", "").replace("%", "").strip()
        for v in samples[:5]
        if v not in ("", None)
    ]
    if not cleaned:
        return False
    # count values that actually parse (float("abc") raises -> excluded)
    n = 0
    for c in cleaned:
        try:
            float(c)
            n += 1
        except Exception:
            pass
    return n >= len(cleaned) * 0.6 and n > 0


def extract_col_from_expr(expr: str) -> str:
    """SUM(revenue) -> revenue ; 'revenue' -> revenue."""
    e = (expr or "").strip().strip("\"'`")
    if "(" in e and ")" in e:
        m = re.search(r"\((.*)\)", e)
        if m:
            return m.group(1).strip().strip("\"'`")
    return e.strip().strip("\"'`")


def alias_block(columns: list[str], role_map: dict[str, str] | None = None) -> str:
    """Render alias help block for the LLM prompt.

    Only lists aliases whose target column actually exists, e.g.:
      "revenue" / "sales" / "amount" -> "GROSS AMT"
    """
    role_map = role_map or {}
    lines: list[str] = []
    for concept, members in CONCEPTS.items():
        targets = [c for c in columns if concept in _concepts_of_toks(tokens(c)) or normalize(c) in members]
        if not targets:
            continue
        # list every real target so the LLM can choose (e.g. month -> DATE
        # or Months, product -> Style or SKU) instead of being pushed to
        # whichever column happens to come first.
        targets_txt = " / ".join(f'"{t}"' for t in targets)
        triggers = sorted({m for m in members if all(normalize(m) != normalize(t) for t in targets)})[:6]
        if triggers:
            quoted = " / ".join(f'"{t}"' for t in triggers)
            lines.append(f"{quoted} -> {targets_txt}")
        else:
            lines.append(f"column(s): {targets_txt}")
    return "\n".join(lines)
