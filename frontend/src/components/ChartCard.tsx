import { useEffect, useRef } from 'react'
import type { ChartResult } from '../lib/store'

declare const Plotly: any

export function ChartCard({ result, onSelect, selected }: { result: ChartResult; onSelect: () => void; selected: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const spec = result.spec
  const hasFigure = !!result.figure_json

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
    // Use Plotly from CDN if available, otherwise fallback to div
    const PlotlyAny: any = (window as any).Plotly
    if (PlotlyAny?.newPlot) {
      PlotlyAny.newPlot(ref.current, fig.data, fig.layout, { displayModeBar: false, responsive: true })
      const onResize = () => PlotlyAny.Plots.resize(ref.current)
      window.addEventListener('resize', onResize)
      return () => {
        window.removeEventListener('resize', onResize)
        try { PlotlyAny.purge(ref.current) } catch {}
      }
    } else {
      // No Plotly yet — render placeholder and lazy load by injecting script once
      // For now show JSON placeholder
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
