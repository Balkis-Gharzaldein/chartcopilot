import { useEffect, useRef, useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { applyThemeToFigure } from '../lib/chartTheme'
import { loadPlotly, normalizePlotlyFigure } from '../lib/plotly'

function DrawerChartPreview({ figureJson, title }: { figureJson: string; title: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => {
    if (!ref.current || !figureJson) return
    let fig: any
    try { fig = normalizePlotlyFigure(JSON.parse(figureJson)) } catch { return }
    applyThemeToFigure(fig, title)
    fig.layout = { ...fig.layout, autosize: true, margin: { t: 32, r: 12, b: 32, l: 48 }, height: 220 }
    let cancelled = false
    let PlotlyAny: any
    loadPlotly().then((plotly) => {
      if (cancelled || !ref.current) return
      PlotlyAny = plotly
      PlotlyAny.newPlot(ref.current, fig.data, fig.layout, { displayModeBar: false, responsive: true }).then(() => {
        if (!cancelled) setReady(true)
      })
    }).catch(() => setReady(false))
    return () => {
      cancelled = true
      if (ref.current && PlotlyAny?.purge) PlotlyAny.purge(ref.current)
    }
  }, [figureJson, title])
  return <div ref={ref} className={`h-[220px] w-full rounded-xl border border-slate-200 bg-white ${ready ? 'fade-slide' : ''}`} />
}

export function ChartDrawer() {
  const { results, activeChartId, setActiveChart, workbookId, setResults, specs } = useStore()
  const tab = (useStore as any)((s: any) => s.drawerTab ?? 'data')
  const setTab = (useStore as any)((s: any) => s.setDrawerTab)
  const addToast = (useStore as any)((s: any) => s.addToast)
  const active = results.find(r => r.spec.id === activeChartId) || null
  const [refineMsg, setRefineMsg] = useState('')
  const [refining, setRefining] = useState(false)
  const [reply, setReply] = useState<string | null>(null)

  if (!active) return null

  const close = () => { setActiveChart(null); setTab('data') }

  const goalMap: Record<string,string> = {
    bar:'Comparison', horizontal_bar:'Ranking', grouped_bar:'Comparison', stacked_bar:'Composition', stacked_100:'Composition',
    line:'Trend', area:'Trend', scatter:'Relationship', histogram:'Distribution', boxplot:'Distribution', heatmap:'Correlation', pie:'Composition', donut:'Composition'
  }
  const goal = goalMap[active.spec.chart_type] || 'Overview'

  async function doRefine() {
    if (!refineMsg.trim()) return
    if (!workbookId) {
      const msg = 'No workbook loaded. Re-upload the dataset, then refine.'
      setReply(msg)
      addToast?.(msg, 'error')
      return
    }
    setRefining(true)
    setReply(null)
    try {
      const idx = results.indexOf(active!)
      const res = await api.refine(workbookId, refineMsg.trim(), idx)
       setResults(res.results, res.results.map((result: any) => result.spec), res.narrative)
      setReply(res.reply)
      setRefineMsg('')
    } catch (e: any) {
      const msg = e.message || 'Refine failed'
      setReply(msg)
      addToast?.(msg, 'error')
    } finally { setRefining(false) }
  }

  const downloadPNG = () => {
    // Find the ChartCard's gd by title? Instead, use preview's div if we had ref, but simpler: find by active id
    // For drawer, we can reuse the preview's Plotly instance if we store ref, but for now just download from preview
    // We'll trigger download via the preview's div
    const gd = document.querySelector(`[data-drawer-chart="${active.spec.id}"]`) as any
    const PlotlyAny: any = (window as any).Plotly
    if (gd && PlotlyAny?.downloadImage) {
      const base = active.spec.title.trim().toLowerCase().replace(/\s+/g,'-').replace(/[^a-z0-9\-_]+/g,'-').slice(0,60) || 'chart'
      PlotlyAny.downloadImage(gd, { format: 'png', width: 1600, height: 900, filename: base, scale: 2 })
    } else {
      addToast?.('Chart image is still loading — try again in a moment.', 'info')
    }
  }

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-[1px] fade-slide" onClick={close} />
      <div className="absolute right-0 top-0 h-full w-full sm:w-[440px] max-w-[92vw] bg-slate-50 border-l border-slate-200 shadow-xl flex flex-col fade-slide sm:rounded-l-2xl overflow-hidden">
        <div className="h-14 flex items-center justify-between px-4 bg-white border-b border-slate-200 shrink-0">
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate text-slate-900">{active.spec.title}</p>
            <p className="text-[11px] text-slate-500 truncate flex items-center gap-1.5">
              <span className="px-1.5 py-0.5 rounded bg-slate-900 text-white text-[10px]">{active.spec.chart_type}</span>
              <span>{goal}</span>
              {active.verified && <span className="px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px]">Verified ✓</span>}
            </p>
          </div>
          <button onClick={close} className="h-8 w-8 rounded-full hover:bg-slate-100 flex items-center justify-center shrink-0 text-slate-600">✕</button>
        </div>

        <div className="p-3 bg-white border-b border-slate-100">
          {active.figure_json ? (
            <div data-drawer-chart={active.spec.id}>
              <DrawerChartPreview figureJson={active.figure_json} title={active.spec.title} />
            </div>
          ) : (
            <div className="h-[220px] rounded-xl border border-dashed border-slate-200 bg-slate-50 flex items-center justify-center text-xs text-slate-500">No chart preview</div>
          )}
          <div className="mt-2 flex gap-2">
            <button onClick={downloadPNG} className="flex-1 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-medium hover:bg-slate-800">Download PNG</button>
            <button onClick={() => setTab('refine')} className="flex-1 py-1.5 rounded-lg bg-amber-500 text-white text-xs font-medium hover:bg-amber-600">Refine</button>
          </div>
        </div>

        <div className="flex gap-1 p-2 border-b border-slate-200 bg-slate-50 shrink-0">
          {(['data','computed','validation','refine'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${tab===t ? 'bg-slate-900 text-white shadow' : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-50'}`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto p-4 bg-slate-50">
          {tab === 'data' && (
            <div key="data" className="space-y-3 fade-slide">
              {active.figure_data?.length ? (
                <div className="rounded-xl border border-slate-200 overflow-hidden bg-white card-shadow">
                  <div className="max-h-[320px] overflow-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-white border-b border-slate-200">
                        <tr>
                          {Object.keys(active.figure_data[0] || {}).map(k => (
                            <th key={k} className="px-3 py-2 text-left font-medium text-slate-600">{k}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {active.figure_data.slice(0,120).map((row,i) => (
                          <tr key={i} className="border-t border-slate-100 hover:bg-slate-50/60">
                            {Object.values(row).map((v:any, j) => (
                              <td key={j} className="px-3 py-1.5 truncate max-w-[140px] text-slate-700">{String(v ?? '')}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {active.figure_data.length > 120 && <p className="text-[11px] text-slate-500 px-3 py-2 border-t">Showing 120 of {active.figure_data.length} rows</p>}
                </div>
              ) : (
                <p className="text-sm text-slate-500">No tabular rows for this chart.</p>
              )}
              {active.adaptation_note && <p className="text-xs text-slate-600 bg-white border border-slate-200 rounded-xl p-3 card-shadow">Note: {active.adaptation_note}</p>}
            </div>
          )}

          {tab === 'computed' && (
            <div key="computed" className="fade-slide">
              <div className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                <p className="text-xs font-semibold text-slate-900 mb-2">Computed Summary</p>
                <pre className="text-xs bg-slate-50 text-slate-800 rounded-lg p-3 overflow-auto max-h-[420px] border border-slate-200">{JSON.stringify(active.computed_summary, null, 2)}</pre>
              </div>
              <div className="mt-3 bg-white rounded-xl border border-slate-200 card-shadow p-3">
                <p className="text-xs font-medium text-slate-700">Data used</p>
                <p className="text-[11px] text-slate-500 mt-1">{active.spec.x ? `x: ${active.spec.x}` : ''} {active.spec.y ? `· y: ${active.spec.y}` : ''} {active.spec.group_by ? `· group: ${active.spec.group_by}` : ''}</p>
              </div>
            </div>
          )}

          {tab === 'validation' && (
            <div key="validation" className="space-y-3 text-xs fade-slide">
              <div className={`rounded-xl border p-3 card-shadow ${active.verified ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
                <p className="font-medium">{active.verified ? '✓ Verified against source data' : `Verification: ${(active.verification as any)?.failed?.join(', ') || 'see checks'}`}</p>
                <p className="text-[11px] opacity-80 mt-1">Independent recomputation {active.verified ? 'matched' : 'did not match'} computed summary.</p>
              </div>
              {(active.validation?.warnings?.length ?? 0) > 0 && (
                <div className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                  <p className="text-xs font-medium text-slate-900">Warnings</p>
                  {active.validation!.warnings.map((w:string,i:number) => (
                    <p key={i} className="text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5 mt-2 text-xs">{w}</p>
                  ))}
                </div>
              )}
              {(active.validation?.errors?.length ?? 0) > 0 && (
                <div className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                  <p className="text-xs font-medium text-slate-900">Errors</p>
                  {active.validation!.errors.map((e:string,i:number) => (
                    <p key={i} className="text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1.5 mt-2 text-xs">{e}</p>
                  ))}
                </div>
              )}
              {!((active.validation?.warnings?.length ?? 0) > 0) && !((active.validation?.errors?.length ?? 0) > 0) && (
                <p className="text-slate-500 bg-white rounded-xl border border-slate-200 card-shadow p-3">No semantic issues detected.</p>
              )}
              <details className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                <summary className="cursor-pointer font-medium text-slate-900">Raw verification</summary>
                <pre className="mt-2 text-[11px] overflow-auto bg-slate-50 p-2 rounded-lg border border-slate-200">{JSON.stringify(active.verification, null, 2)}</pre>
              </details>
            </div>
          )}

          {tab === 'refine' && (
            <div key="refine" className="space-y-3 fade-slide">
              <div className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                <p className="text-xs font-semibold text-slate-900">Refine this chart</p>
                <p className="text-xs text-slate-500 mt-1">Use natural language — the backend will re-run intent → gates → ranking.</p>
                <textarea
                  value={refineMsg}
                  onChange={e => setRefineMsg(e.target.value)}
                  placeholder='e.g. "Make this a stacked bar chart" or "Show only the top 10 categories"'
                  rows={3}
                  className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-amber-500"
                />
                <button
                  onClick={doRefine}
                  disabled={refining || !refineMsg.trim()}
                  className="mt-3 w-full h-9 rounded-xl bg-amber-500 text-white text-sm font-medium hover:bg-amber-600 disabled:opacity-40"
                >
                  {refining ? 'Refining…' : 'Refine chart'}
                </button>
                {reply && <div className="mt-3 text-xs bg-slate-50 border border-slate-200 rounded-xl p-3 whitespace-pre-wrap">{reply}</div>}
              </div>
              <div className="bg-white rounded-xl border border-slate-200 card-shadow p-3">
                <p className="text-xs font-medium text-slate-900">Try</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button onClick={() => setRefineMsg('Make this a stacked bar chart')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Stacked bar</button>
                  <button onClick={() => setRefineMsg('Show only the top 10 categories')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Top 10</button>
                  <button onClick={() => setRefineMsg('Use a different grouping')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Different grouping</button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
