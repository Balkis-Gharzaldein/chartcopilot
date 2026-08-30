import { useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'

export function ChartDrawer() {
  const { results, activeChartId, setActiveChart, workbookId, setResults, specs } = useStore()
  const active = results.find(r => r.spec.id === activeChartId) || null
  const [tab, setTab] = useState<'data'|'computed'|'validation'|'refine'>('data')
  const [refineMsg, setRefineMsg] = useState('')
  const [refining, setRefining] = useState(false)
  const [reply, setReply] = useState<string | null>(null)

  if (!active) return null

  async function doRefine() {
    if (!refineMsg.trim() || !workbookId) return
    setRefining(true)
    setReply(null)
    try {
      const idx = results.indexOf(active!)
      const res = await api.refine(workbookId, refineMsg.trim(), idx)
      setResults(res.results, specs, res.narrative)
      setReply(res.reply)
      setRefineMsg('')
    } catch (e: any) {
      setReply(e.message || 'Refine failed')
    } finally { setRefining(false) }
  }

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/20" onClick={() => setActiveChart(null)} />
      <div className="absolute right-0 top-0 h-full w-[420px] max-w-[92vw] bg-white border-l shadow-xl flex flex-col">
        <div className="h-12 flex items-center justify-between px-4 border-b shrink-0">
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate">{active.spec.title}</p>
            <p className="text-xs text-zinc-500 truncate">{active.spec.chart_type} · {active.spec.x} {active.spec.y ? `→ ${active.spec.y}` : ''}</p>
          </div>
          <button onClick={() => setActiveChart(null)} className="h-8 w-8 rounded-full hover:bg-zinc-100 flex items-center justify-center shrink-0">✕</button>
        </div>

        <div className="flex gap-1 p-2 border-b bg-zinc-50 shrink-0">
          {(['data','computed','validation','refine'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium capitalize ${tab===t ? 'bg-zinc-900 text-white' : 'hover:bg-white border border-transparent hover:border-zinc-200'}`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {tab === 'data' && (
            <div className="space-y-3">
              {active.figure_data?.length ? (
                <div className="rounded-xl border overflow-hidden">
                  <div className="max-h-[420px] overflow-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-zinc-50">
                        <tr>
                          {Object.keys(active.figure_data[0] || {}).map(k => (
                            <th key={k} className="px-2 py-1.5 text-left font-medium text-zinc-600 border-b">{k}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {active.figure_data.slice(0,120).map((row,i) => (
                          <tr key={i} className="border-t">
                            {Object.values(row).map((v:any, j) => (
                              <td key={j} className="px-2 py-1 truncate max-w-[140px]">{String(v ?? '')}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {active.figure_data.length > 120 && <p className="text-[11px] text-zinc-500 px-2 py-1">Showing 120 of {active.figure_data.length} rows</p>}
                </div>
              ) : (
                <p className="text-sm text-zinc-500">No tabular rows for this chart.</p>
              )}
              {active.adaptation_note && <p className="text-xs text-zinc-500 bg-zinc-50 border rounded-lg p-2">Note: {active.adaptation_note}</p>}
            </div>
          )}

          {tab === 'computed' && (
            <pre className="text-xs bg-zinc-950 text-zinc-100 rounded-xl p-3 overflow-auto max-h-[520px]">{JSON.stringify(active.computed_summary, null, 2)}</pre>
          )}

          {tab === 'validation' && (
            <div className="space-y-3 text-xs">
              <div className={`rounded-lg border px-3 py-2 ${active.verified ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
                {active.verified ? '✓ Verified against source data' : `Verification: ${(active.verification as any)?.failed?.join(', ') || 'see checks'}`}
              </div>
              {(active.validation?.warnings?.length ?? 0) > 0 && (
                <div className="space-y-1">
                  <p className="font-medium">Warnings</p>
                  {active.validation!.warnings.map((w:string,i:number) => (
                    <p key={i} className="text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">{w}</p>
                  ))}
                </div>
              )}
              {(active.validation?.errors?.length ?? 0) > 0 && (
                <div className="space-y-1">
                  <p className="font-medium">Errors</p>
                  {active.validation!.errors.map((e:string,i:number) => (
                    <p key={i} className="text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1">{e}</p>
                  ))}
                </div>
              )}
              {!((active.validation?.warnings?.length ?? 0) > 0) && !((active.validation?.errors?.length ?? 0) > 0) && (
                <p className="text-zinc-500">No semantic issues detected.</p>
              )}
              <details className="rounded-lg border p-2">
                <summary className="cursor-pointer font-medium">Raw verification</summary>
                <pre className="mt-2 text-[11px] overflow-auto">{JSON.stringify(active.verification, null, 2)}</pre>
              </details>
            </div>
          )}

          {tab === 'refine' && (
            <div className="space-y-3">
              <p className="text-xs text-zinc-500">Edit this chart with natural language — e.g. "Make it a stacked bar", "Show top 10", "Focus on Q1"</p>
              <textarea
                value={refineMsg}
                onChange={e => setRefineMsg(e.target.value)}
                placeholder='e.g. "Make this a bar chart" or "Show top 10 categories"'
                rows={3}
                className="w-full rounded-xl border border-zinc-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
              />
              <button
                onClick={doRefine}
                disabled={refining || !refineMsg.trim()}
                className="w-full h-9 rounded-xl bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-800 disabled:opacity-40"
              >
                {refining ? 'Refining…' : 'Refine chart'}
              </button>
              {reply && <div className="text-xs bg-zinc-50 border rounded-xl p-2 whitespace-pre-wrap">{reply}</div>}
              <div className="text-xs text-zinc-500 space-y-1">
                <p>Try:</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li><button onClick={() => setRefineMsg('Make it a bar chart')} className="underline hover:text-zinc-700">Make it a bar chart</button></li>
                  <li><button onClick={() => setRefineMsg('Show top 10 categories')} className="underline hover:text-zinc-700">Show top 10</button></li>
                  <li><button onClick={() => setRefineMsg('Make it a horizontal bar')} className="underline hover:text-zinc-700">Make it horizontal</button></li>
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
