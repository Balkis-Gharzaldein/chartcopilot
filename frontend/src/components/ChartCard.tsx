import { useEffect, useRef, useState } from 'react'
import type { ChartResult } from '../lib/store'

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

export function ChartCard({ result, onSelect, selected }: { result: ChartResult; onSelect: () => void; selected: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const spec = result.spec
  const hasFigure = !!result.figure_json
  const [ready, setReady] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    if (!hasFigure || !ref.current || !result.figure_json) return
    let fig: any
    try { fig = JSON.parse(result.figure_json) } catch { return }
    // Ensure responsive layout
    fig.layout = {
      ...fig.layout,
      autosize: true,
      margin: { t: 40, r: 12, b: 40, l: 60 },
      font: { size: 11 },
      paper_bgcolor: 'white',
      plot_bgcolor: 'white',
    }
    // Use Plotly from CDN if available
    const PlotlyAny: any = (window as any).Plotly
    if (PlotlyAny?.newPlot) {
      setReady(false)
      PlotlyAny.newPlot(ref.current, fig.data, fig.layout, { displayModeBar: false, responsive: true }).then(() => setReady(true))
      const onResize = () => PlotlyAny.Plots.resize(ref.current)
      window.addEventListener('resize', onResize)
      return () => {
        window.removeEventListener('resize', onResize)
        setReady(false)
        try { PlotlyAny.purge(ref.current) } catch {}
      }
    }
  }, [result.figure_json, hasFigure])

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

  return (
    <div
      onClick={onSelect}
      className={`group bg-white rounded-2xl border shadow-sm hover:shadow-md transition-shadow overflow-hidden cursor-pointer ${selected ? 'ring-2 ring-zinc-900 border-zinc-900' : 'border-zinc-200'}`}
    >
      <div className="px-4 py-3 flex items-start justify-between gap-3 border-b bg-zinc-50/60">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold truncate" title={spec.title}>{spec.title}</h3>
          <p className="text-xs text-zinc-500 truncate">
            {spec.chart_type} {spec.x ? `· ${spec.x}` : ''} {spec.y ? `→ ${spec.y}` : ''} {spec.agg_function ? `· ${spec.agg_function}` : ''}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-1.5">
          {result.verified ? (
            <span className="text-[11px] px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">Verified</span>
          ) : result.verification && Object.keys(result.verification).length > 0 ? (
            <span className="text-[11px] px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200">Needs check</span>
          ) : null}
          {hasFigure && spec.status !== 'skipped' && (
            <div className="relative" data-chart-menu onClick={e => e.stopPropagation()}>
              <button
                onClick={() => setMenuOpen(v => !v)}
                className="h-7 w-7 rounded-full hover:bg-white border border-transparent hover:border-zinc-200 flex items-center justify-center text-zinc-600"
                aria-label="Chart actions"
                title="Download"
              >
                ⋯
              </button>
              {menuOpen && (
                <div className="absolute right-0 top-8 w-44 bg-white border border-zinc-200 rounded-xl shadow-lg py-1 z-20">
                  <button
                    onClick={() => handleDownload('png')}
                    disabled={!ready}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download PNG
                  </button>
                  <button
                    onClick={() => handleDownload('svg')}
                    disabled={!ready}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download SVG
                  </button>
                  <button
                    onClick={handleCSV}
                    disabled={!result.figure_data || result.figure_data.length === 0}
                    className="w-full text-left px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Download CSV
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="p-2">
        {spec.status === 'skipped' ? (
          <div className="h-[240px] flex items-center justify-center text-xs text-amber-700 bg-amber-50 rounded-xl border border-amber-200 m-1 px-4 text-center">
            Skipped: {spec.skip_reason}
          </div>
        ) : hasFigure ? (
          <div ref={ref} className="h-[300px] w-full" />
        ) : (
          <div className="h-[300px] flex items-center justify-center text-sm text-zinc-500 bg-zinc-50 rounded-xl m-1">
            {result.adaptation_note || 'No figure'}
          </div>
        )}
      </div>

      {(result.adaptation_note || result.validation?.warnings?.length > 0) && (
        <div className="px-4 pb-3 space-y-1">
          {result.adaptation_note && <p className="text-[11px] text-zinc-500">Note: {result.adaptation_note}</p>}
          {result.validation?.warnings?.slice(0,2).map((w: string, i:number) => (
            <p key={i} className="text-[11px] text-amber-600">⚠ {w}</p>
          ))}
        </div>
      )}
    </div>
  )
}
