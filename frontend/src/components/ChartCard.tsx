import { useEffect, useRef, useState } from 'react'
import { useStore } from '../lib/store'
import type { ChartResult } from '../lib/store'
import { applyThemeToFigure } from '../lib/chartTheme'

declare const Plotly: any

function sanitizeFilename(title: string): string {
  const base = title.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9\-_]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  // Remove Windows invalid chars <>:"/\|?* and control
  const cleaned = base.replace(/[<>:"/\\|?*\x00-\x1F]/g, '').slice(0, 80) || 'chart'
  return cleaned
}

function downloadCSV(figureData: any[], filename: string) {
  if (!figureData || figureData.length === 0) return
  const cols = Object.keys(figureData[0])
  const escape = (v: any) => {
    const s = String(v ?? '')
    if (s.includes('"') || s.includes(',') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"'
    return s
  }
  const rows = [cols.map(escape).join(',')].concat(figureData.map(r => cols.map(c => escape((r as any)[c])).join(',')))
  const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url) }, 0)
}

const goalMap: Record<string, string> = {
  bar: 'Comparison',
  horizontal_bar: 'Ranking',
  grouped_bar: 'Comparison',
  stacked_bar: 'Composition',
  stacked_100: 'Composition',
  line: 'Trend',
  area: 'Trend',
  scatter: 'Relationship',
  histogram: 'Distribution',
  boxplot: 'Distribution',
  heatmap: 'Correlation',
  pie: 'Composition',
  donut: 'Composition',
}

export function ChartCard({ result, onSelect, selected }: { result: ChartResult; onSelect: () => void; selected: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const spec = result.spec
  const hasFigure = !!result.figure_json
  const [ready, setReady] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  const isBar = spec.chart_type.includes('bar')
  const isLine = spec.chart_type === 'line' || spec.chart_type === 'area'
  const goal = goalMap[spec.chart_type] || 'Overview'
  const shortDesc = (() => {
    const dn = (spec.data_notes || '').replace(/\[score[^]]*\]/g, '').trim()
    if (dn && dn.length < 90) return dn
    if (spec.x && spec.y) return `${spec.y} by ${spec.x}` + (spec.group_by ? ` · ${spec.group_by}` : '')
    if (spec.x) return `By ${spec.x}`
    return ''
  })()

  useEffect(() => {
    if (!hasFigure || !ref.current || !result.figure_json) return
    let fig: any
    try { fig = JSON.parse(result.figure_json) } catch { return }
    // Apply centralized theme — ensures same palette as backend, not old blue/red
    applyThemeToFigure(fig, spec.title)
    // Ensure responsive + hover tooltips on all chart types (additive, non-breaking)
    fig.layout = {
      ...fig.layout,
      autosize: true,
      hovermode: 'closest',
      // Enable native zoom/pan when x-axis is dense; Plotly supports it via dragmode
      dragmode: fig.layout?.xaxis?.type === 'category' && (fig.data?.[0]?.x?.length || 0) > 20 ? 'pan' : fig.layout.dragmode,
    }
    // Enable hover templates for precise values if not already set by backend
    fig.data = (fig.data || []).map((tr: any) => ({
      ...tr,
      hovertemplate: tr.hovertemplate || undefined, // keep backend template, fallback to Plotly default which shows x/y
    }))
    const PlotlyAny: any = (window as any).Plotly
    if (PlotlyAny?.newPlot) {
      setReady(false)
      const config: any = { displayModeBar: false, responsive: true, scrollZoom: false }
      // Native zoom/pan only if dense, via config; no custom logic
      if (fig.data?.[0]?.x?.length > 25) {
        config.scrollZoom = false
        // enable mode bar zoom via layout dragmode, not custom
      }
      PlotlyAny.newPlot(ref.current, fig.data, fig.layout, config).then(() => setReady(true))
      const onResize = () => PlotlyAny.Plots.resize(ref.current)
      window.addEventListener('resize', onResize)
      return () => {
        window.removeEventListener('resize', onResize)
        setReady(false)
        try { PlotlyAny.purge(ref.current) } catch {}
      }
    }
  }, [result.figure_json, hasFigure, isBar, isLine])

  // Lazy load Plotly if not present
  useEffect(() => {
    if ((window as any).Plotly) return
    const s = document.createElement('script')
    s.src = 'https://cdn.plot.ly/plotly-2.27.0.min.js'
    s.async = true
    document.head.appendChild(s)
  }, [])

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    const onDoc = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('[data-chart-menu]')) setMenuOpen(false)
    }
    document.addEventListener('click', onDoc)
    return () => document.removeEventListener('click', onDoc)
  }, [menuOpen])

  const filenameBase = sanitizeFilename(spec.title)

  const handleDownload = (format: 'png' | 'svg') => {
    const gd: any = ref.current
    const PlotlyAny: any = (window as any).Plotly
    if (!gd || !PlotlyAny?.downloadImage) return
    // Do not regenerate chart, reuse existing instance
    PlotlyAny.downloadImage(gd, {
      format,
      width: 1600,
      height: 900,
      filename: filenameBase,
      scale: 2,
    }).catch(() => {})
    setMenuOpen(false)
  }

  const handleCSV = () => {
    downloadCSV(result.figure_data || [], `${filenameBase}.csv`)
    setMenuOpen(false)
  }

  const handleRemove = () => {
    const { results, setResults, specs, narrative } = (useStore as any).getState()
    const nextResults = results.filter((r: any) => r.spec.id !== spec.id)
    const nextSpecs = specs.filter((s: any) => s.id !== spec.id)
    setResults(nextResults, nextSpecs, narrative)
    setMenuOpen(false)
  }

  return (
    <div
      onClick={onSelect}
      className={`group bg-white rounded-2xl border card-shadow hover:shadow-md transition-shadow overflow-hidden cursor-pointer ${selected ? 'ring-2 ring-amber-500 border-amber-500' : 'border-slate-200'}`}
    >
      <div className="px-4 py-3 flex items-start justify-between gap-3 border-b border-slate-100 bg-slate-50/60">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold truncate text-slate-900" title={spec.title}>{spec.title}</h3>
          <p className="text-[11px] text-slate-500 truncate flex items-center gap-1.5 mt-0.5">
            <span className="px-1.5 py-0.5 rounded bg-slate-900 text-white text-[10px] leading-none">{spec.chart_type}</span>
            <span>{goal}</span>
            {shortDesc && <span className="hidden sm:inline">· {shortDesc}</span>}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          {result.verified ? (
            <span key={`${result.spec.id}-verified`} className="verified-pulse text-[11px] px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">Verified ✓</span>
          ) : result.verification && Object.keys(result.verification).length > 0 ? (
            <span className="text-[11px] px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200">Needs check</span>
          ) : null}
          {hasFigure && spec.status !== 'skipped' && (
            <div className="relative" data-chart-menu onClick={e => e.stopPropagation()}>
              <button
                onClick={() => setMenuOpen(v => !v)}
                className="h-7 w-7 rounded-full hover:bg-white border border-transparent hover:border-slate-200 flex items-center justify-center text-slate-600"
                aria-label="Chart actions"
                title="Menu"
              >
                ⋯
              </button>
              {menuOpen && (
                <div className="absolute right-0 top-8 w-48 bg-white border border-slate-200 rounded-xl shadow-lg py-1 z-20">
                  <button onClick={() => { setMenuOpen(false); (useStore as any).getState().setDrawerTab?.('data'); onSelect() }} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50">
                    View details
                  </button>
                  <button onClick={() => { setMenuOpen(false); (useStore as any).getState().setDrawerTab?.('refine'); onSelect() }} className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50" title="Open details on the refine tab">
                    Refine
                  </button>
                  <div className="my-1 border-t border-slate-100" />
                  <button
                    onClick={() => handleDownload('png')}
                    disabled={!ready}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download PNG
                  </button>
                  <button
                    onClick={() => handleDownload('svg')}
                    disabled={!ready}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download SVG
                  </button>
                  <button
                    onClick={handleCSV}
                    disabled={!result.figure_data || result.figure_data.length === 0}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download CSV
                  </button>
                  <div className="my-1 border-t border-slate-100" />
                  <button onClick={handleRemove} className="w-full text-left px-3 py-1.5 text-xs hover:bg-red-50 text-red-600">
                    Remove
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className={`p-2 ${isBar ? 'bar-grow' : isLine ? 'line-draw' : 'fade-slide'}`}>
        {spec.status === 'skipped' ? (
          <div className="h-[240px] flex items-center justify-center text-xs text-amber-700 bg-amber-50 rounded-xl border border-amber-200 m-1 px-4 text-center">
            Skipped: {spec.skip_reason}
          </div>
        ) : hasFigure ? (
          <div ref={ref} className="h-[300px] w-full" />
        ) : (
          <div className="h-[300px] flex items-center justify-center text-sm text-slate-500 bg-slate-50 rounded-xl m-1">
            {result.adaptation_note || 'No figure'}
          </div>
        )}
      </div>

      <div className="px-4 pb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button onClick={e => { e.stopPropagation(); (useStore as any).getState().setDrawerTab?.('data'); onSelect() }} className="text-[11px] px-2.5 py-1 rounded-full bg-slate-900 text-white hover:bg-slate-800">
            View details
          </button>
          <span className="text-[11px] text-slate-500">{shortDesc}</span>
        </div>
        <button onClick={handleCSV} className="text-[11px] text-slate-500 hover:text-slate-700 underline">
          CSV
        </button>
      </div>

      {(result.adaptation_note || result.validation?.warnings?.length > 0) && (
        <div className="px-4 pb-3 space-y-1 border-t border-slate-100 pt-2">
          {result.adaptation_note && <p className="text-[11px] text-slate-500">Note: {result.adaptation_note}</p>}
          {result.validation?.warnings?.slice(0,2).map((w: string, i:number) => (
            <p key={i} className="text-[11px] text-amber-600">⚠ {w}</p>
          ))}
        </div>
      )}
    </div>
  )
}
