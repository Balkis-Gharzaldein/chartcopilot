/**
 * Centralized visualization theme — single source for all frontend chart rendering.
 * Mirrors backend tools/chart_theme.py.
 * All frontend chart rendering must import from here — no hardcoded colors in components.
 */

export const CATEGORICAL = [
  "#B8A9C9", // soft lavender
  "#8ABAC3", // soft teal
  "#A8C3B9", // sage green
  "#D8C4A8", // warm beige
  "#8FA8C8", // dusty blue
  "#E8B8A0", // muted peach
  "#9A8CB4", // muted purple
  "#E6D5A0", // soft sand
  "#B8D1C2",
  "#C9B8A8",
]

// Professional role colors — single source for KpiCard accent + DatasetProfile badges
export const ROLE_COLORS = {
  numeric: { bg: 'bg-sky-50', text: 'text-sky-700', border: 'border-sky-200', accent: '#0EA5E9', dot: 'bg-sky-500' },
  categorical: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200', accent: '#F59E0B', dot: 'bg-amber-500' },
  temporal: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200', accent: '#10B981', dot: 'bg-emerald-500' },
  identifier: { bg: 'bg-slate-100', text: 'text-slate-600', border: 'border-slate-200', accent: '#64748B', dot: 'bg-slate-400' },
} as const

// Unified signals: good → green (ok), warning → amber (check), critical → red (fix)
export const SIGNAL = {
  good: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200', dot: 'bg-emerald-500', label: 'good' },
  warning: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200', dot: 'bg-amber-500', label: 'warning' },
  critical: { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200', dot: 'bg-red-500', label: 'critical' },
  neutral: { bg: 'bg-slate-50', text: 'text-slate-600', border: 'border-slate-200', dot: 'bg-slate-400', label: 'neutral' },
} as const

export function signalForNull(pct: number) {
  if (pct > 20) return SIGNAL.critical
  if (pct > 5) return SIGNAL.warning
  if (pct > 0) return SIGNAL.neutral
  return SIGNAL.good
}
export function signalForSkew(skew: number | null) {
  if (skew == null) return SIGNAL.neutral
  const a = Math.abs(skew)
  if (a > 2) return SIGNAL.critical
  if (a > 1) return SIGNAL.warning
  return SIGNAL.good
}
export function signalForOutlier(pct: number | null) {
  if (pct == null || pct === 0) return SIGNAL.good
  if (pct > 5) return SIGNAL.critical
  if (pct > 2) return SIGNAL.warning
  return SIGNAL.neutral
}
export function signalForCorr(v: number) {
  const a = Math.abs(v)
  if (a >= 0.7) return SIGNAL.good
  if (a >= 0.5) return SIGNAL.warning
  return SIGNAL.neutral
}

export const PRIMARY = "#9A8CB4"

export const DIVERGING_SCALE: [number, string][] = [
  [0, "#6BA8B5"],
  [0.16, "#8ABAC3"],
  [0.33, "#B8D1D6"],
  [0.5, "#F5F3EF"],
  [0.66, "#E8D0B8"],
  [0.83, "#E8B8A0"],
  [1, "#D8A08A"],
]

export const SEQUENTIAL_SCALE: [number, string][] = [
  [0, "#F2F7F6"],
  [0.16, "#E0ECE8"],
  [0.33, "#C8DDD6"],
  [0.5, "#B0D0C8"],
  [0.66, "#8ABAC3"],
  [0.83, "#6BA8B5"],
  [1, "#4A8A9A"],
]

export const LAYOUT = {
  paper_bgcolor: "#FFFFFF",
  plot_bgcolor: "#FFFFFF",
  font_family: "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial",
  font_color: "#1F2937",
  title_color: "#0F172A",
  grid_color: "#E5E7EB",
  axis_line_color: "#E5E7EB",
}

export function getCategorical(n: number): string[] {
  if (n <= 0) return [PRIMARY]
  return Array.from({ length: n }, (_, i) => CATEGORICAL[i % CATEGORICAL.length])
}

export function applyThemeToFigure(fig: any, title?: string) {
  // Mutates figure in place to ensure consistent theme even if backend figure used old defaults
  // Only sets layout-level theme, does not overwrite trace colors already set by backend
  fig.layout = {
    ...fig.layout,
    template: "plotly_white",
    paper_bgcolor: LAYOUT.paper_bgcolor,
    plot_bgcolor: LAYOUT.plot_bgcolor,
    font: { family: LAYOUT.font_family, color: LAYOUT.font_color, size: 11 },
    title: {
      text: title || fig.layout?.title?.text || "",
      x: 0.02,
      xanchor: "left",
      font: { size: 14, color: LAYOUT.title_color, family: LAYOUT.font_family },
      pad: { t: 8, b: 8 },
    },
    margin: { l: 60, r: 24, t: 48, b: 56, pad: 4 },
    colorway: CATEGORICAL,
    legend: {
      bgcolor: "rgba(255,255,255,0.9)",
      bordercolor: "#E5E7EB",
      borderwidth: 1,
      font: { size: 10, color: LAYOUT.font_color },
      orientation: "h",
      yanchor: "bottom",
      y: 1.02,
      xanchor: "right",
      x: 1,
    },
    hoverlabel: { bgcolor: "white", bordercolor: "#E5E7EB", font: { size: 11 } },
  }
  // Ensure axes use light grid
  const axisUpdate = {
    showgrid: true,
    gridcolor: LAYOUT.grid_color,
    linecolor: LAYOUT.axis_line_color,
    ticks: "outside",
    tickcolor: LAYOUT.axis_line_color,
    tickfont: { size: 10, color: "#6B7280" },
    title: { font: { size: 11, color: "#374151" } },
  }
  fig.layout.xaxis = { ...axisUpdate, ...(fig.layout.xaxis || {}) }
  fig.layout.yaxis = { ...axisUpdate, ...(fig.layout.yaxis || {}) }
  // If trace has no explicit color, ensure it uses PRIMARY or CATEGORICAL via colorway
  return fig
}
