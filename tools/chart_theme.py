"""Centralized visualization theme for ChartCopilot.

Soft, sophisticated palette designed for white / very light backgrounds.
Avoids saturated red/blue; red used only for semantic warnings (not in palette).
All chart builders should import from here — no hardcoded colors in individual builders.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

# Harmonious categorical — soft pastel / muted, distinct on white
# Order tuned for contrast: alternate cool/warm
CATEGORICAL = [
    "#B8A9C9",  # 0 soft lavender
    "#8ABAC3",  # 1 soft teal
    "#A8C3B9",  # 2 sage green
    "#D8C4A8",  # 3 warm beige
    "#8FA8C8",  # 4 dusty blue
    "#E8B8A0",  # 5 muted peach
    "#9A8CB4",  # 6 muted purple
    "#E6D5A0",  # 7 soft yellow / sand
    "#B8D1C2",  # 8 mint sage (extended)
    "#C9B8A8",  # 9 greige
]

# Primary single-series color — elegant muted purple, used when only one series
PRIMARY = "#9A8CB4"

# Sequential for single-hue intensity (e.g., heatmap sequential, histogram single)
SEQUENTIAL_TEAL = [
    "#F2F7F6",
    "#E0ECE8",
    "#C8DDD6",
    "#B0D0C8",
    "#8ABAC3",
    "#6BA8B5",
    "#4A8A9A",
]

# Diverging for correlation / positive-negative — muted teal ↔ warm peach via light neutral
# Used for heatmap correlation (-1 to 1)
DIVERGING = [
    "#6BA8B5",  # -1  soft teal deep
    "#8ABAC3",  # -0.5
    "#B8D1D6",  # -0.25
    "#F5F3EF",  #  0   warm white / beige tint
    "#E8D0B8",  # +0.25
    "#E8B8A0",  # +0.5 peach
    "#D8A08A",  # +1   muted terracotta
]

# Heatmap colorscale for Plotly (list of [position, color])
DIVERGING_SCALE = [[i / (len(DIVERGING) - 1), c] for i, c in enumerate(DIVERGING)]
SEQUENTIAL_SCALE = [[i / (len(SEQUENTIAL_TEAL) - 1), c] for i, c in enumerate(SEQUENTIAL_TEAL)]

# Semantic — only place red is allowed is warning / negative emphasis
SEMANTIC_NEGATIVE = "#D18A8A"  # muted rose, not pure red
SEMANTIC_WARNING = "#E8C99A"  # warm sand for other bucket

# Layout tokens — light, professional dashboard feel
LAYOUT = {
    "paper_bgcolor": "#FFFFFF",
    "plot_bgcolor": "#FFFFFF",
    "font_family": "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial",
    "font_color": "#2B2D42",
    "title_color": "#1F2028",
    "grid_color": "#EDEDED",
    "axis_line_color": "#E5E5E5",
    "legend_bg": "rgba(255,255,255,0.9)",
}


def get_categorical(n: int) -> list[str]:
    """Return first n categorical colors, cycling if needed."""
    if n <= 0:
        return [PRIMARY]
    # Cycle if more than palette length
    return [CATEGORICAL[i % len(CATEGORICAL)] for i in range(n)]


def apply_base_layout(fig, title: str | None = None):
    """Apply shared layout theme to any figure."""
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor=LAYOUT["paper_bgcolor"],
        plot_bgcolor=LAYOUT["plot_bgcolor"],
        font=dict(family=LAYOUT["font_family"], color=LAYOUT["font_color"], size=11),
        title=dict(
            text=title or "",
            x=0.02,
            xanchor="left",
            font=dict(size=14, color=LAYOUT["title_color"], family=LAYOUT["font_family"]),
            pad=dict(t=8, b=8),
        ),
        legend=dict(
            bgcolor=LAYOUT["legend_bg"],
            bordercolor="#E5E5E5",
            borderwidth=1,
            font=dict(size=10, color=LAYOUT["font_color"]),
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        margin=dict(l=60, r=24, t=48, b=56, pad=4),
        colorway=CATEGORICAL,
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=LAYOUT["grid_color"],
        linecolor=LAYOUT["axis_line_color"],
        ticks="outside",
        tickcolor=LAYOUT["axis_line_color"],
        tickfont=dict(size=10, color="#6B7280"),
        title_font=dict(size=11, color="#374151"),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=LAYOUT["grid_color"],
        linecolor=LAYOUT["axis_line_color"],
        ticks="outside",
        tickcolor=LAYOUT["axis_line_color"],
        tickfont=dict(size=10, color="#6B7280"),
        title_font=dict(size=11, color="#374151"),
    )
    return fig
