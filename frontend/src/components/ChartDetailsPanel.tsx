import { useEffect, useRef } from 'react'
import { applyThemeToFigure } from '../lib/chartTheme'
import { RefineBotPanel } from './RefineBotPanel'
import { loadPlotly, normalizePlotlyFigure } from '../lib/plotly'

export function ChartDetailsPanel({ chart, chartIndex, onRefine, onClose }: { chart: any; chartIndex: number; onRefine: (updatedChart: any, log: string) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current || !chart.figure_json) return
    let cancelled = false
    let PlotlyAny: any
    loadPlotly().then((plotly) => {
      if (cancelled || !ref.current) return
      PlotlyAny = plotly
      let fig: any
      try { fig = normalizePlotlyFigure(JSON.parse(chart.figure_json)) } catch { return }
      applyThemeToFigure(fig, chart.spec?.title)
      fig.layout = { ...fig.layout, autosize: true, margin: { t: 32, r: 12, b: 32, l: 48 }, height: 260 }
      PlotlyAny.newPlot(ref.current, fig.data, fig.layout, { displayModeBar: false, responsive: true })
    }).catch(() => {})
    return () => {
      cancelled = true
      if (ref.current && PlotlyAny?.purge) PlotlyAny.purge(ref.current)
    }
  }, [chart.figure_json])

  const download = () => {
    const gd: any = ref.current
    const PlotlyAny: any = (window as any).Plotly
    if (gd && PlotlyAny?.downloadImage) {
      const base = (chart.spec?.title || 'chart').trim().toLowerCase().replace(/\s+/g, '-').slice(0,60) || 'chart'
      PlotlyAny.downloadImage(gd, { format: 'png', width: 1200, height: 600, filename: base, scale: 2 })
    }
  }

  const handleRefine = (updatedChart: any) => {
    onRefine(updatedChart, '')
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900 truncate">{chart.spec?.title || 'Chart Details'}</h3>
        <button onClick={onClose} className="h-7 w-7 rounded-full hover:bg-slate-100 flex items-center justify-center text-slate-600">✕</button>
      </div>

      <div className="rounded-xl border border-slate-200 overflow-hidden bg-white">
        {chart.figure_json ? <div ref={ref} className="h-[260px] w-full" /> : <p className="p-4 text-xs text-slate-500">No chart data</p>}
      </div>

      <div className="flex gap-2">
        <button onClick={download} className="flex-1 py-2 rounded-xl bg-slate-900 text-white text-xs font-medium hover:bg-slate-800">Download PNG ↓</button>
        <button onClick={onClose} className="flex-1 py-2 rounded-xl border border-slate-200 bg-white text-xs font-medium hover:bg-slate-50">Close</button>
      </div>

      <div className="border-t border-slate-200 pt-3">
        <div className="flex gap-1 mb-3">
          <span className="px-2 py-1 rounded-full bg-slate-900 text-white text-[10px]">{chart.spec?.chart_type}</span>
          {chart.verified && <span className="px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px]">Verified ✓</span>}
        </div>
        {chart.figure_data && (
          <div className="max-h-[180px] overflow-auto rounded-lg border border-slate-200">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 sticky top-0">
                <tr>{Object.keys(chart.figure_data[0] || {}).map(k=> <th key={k} className="px-2 py-1 text-left font-medium text-slate-600 border-b">{k}</th>)}</tr>
              </thead>
              <tbody>
                {chart.figure_data.slice(0,5).map((row:any, idx:number)=> (
                  <tr key={idx} className="border-t"><td colSpan={Object.keys(row).length} className="px-2 py-1 text-slate-700">{Object.values(row).slice(0,3).join(' — ')}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <RefineBotPanel chart={chart} chartIndex={chartIndex} onRefine={handleRefine} />
    </div>
  )
}
