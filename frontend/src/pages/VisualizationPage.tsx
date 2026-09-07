import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../lib/store'
import { GenerationSection } from '../components/GenerationSection'
import { ChartCard } from '../components/ChartCard'

export function VisualizationPage() {
  const { workbookId, results, setResults, activeChartId, setActiveChart } = useStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasWorkbook = !!workbookId

  const handleChartsGenerated = (newCharts: any[]) => {
    // For /api/charts/generate, results are new charts; replace or append?
    // Keep existing and append new for multiple generations
    const { setResults: sr, specs, narrative } = useStore.getState() as any
    // If we have existing results, append; otherwise set
    if (results.length > 0) {
      const merged = [...results, ...newCharts]
      // Dedupe by id
      const seen = new Set()
      const deduped = merged.filter((c:any) => {
        if (seen.has(c.spec.id)) return false
        seen.add(c.spec.id)
        return true
      })
      setResults(deduped, [...specs, ...newCharts.map((c:any)=> c.spec)], narrative)
    } else {
      // Use specs from newCharts
      const specsFromCharts = newCharts.map((c:any)=> c.spec)
      setResults(newCharts, specsFromCharts, '')
    }
    setError(null)
  }

  const handleError = (msg: string) => setError(msg)

  const handleRefine = (refinedChart: any) => {
    const idx = results.findIndex(r => r.spec.id === refinedChart.spec.id)
    if (idx === -1) return
    const next = [...results]
    next[idx] = refinedChart
    const { specs, narrative } = useStore.getState() as any
    const nextSpecs = [...specs]
    const sIdx = nextSpecs.findIndex((s:any)=> s.id === refinedChart.spec.id)
    if (sIdx !== -1) nextSpecs[sIdx] = refinedChart.spec
    setResults(next, nextSpecs, narrative)
    setActiveChart(refinedChart.spec.id)
  }

  if (!hasWorkbook) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-8 text-center">
        <p className="text-sm font-medium text-slate-900">No dataset loaded</p>
        <p className="text-xs text-slate-500 mt-1">Upload a dataset on the Dashboard to start asking questions.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col lg:flex-row gap-6">
        {/* LEFT COLUMN: Charts 65% */}
        <div className="flex-1 lg:w-[65%] space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-900">Generated Charts</h2>
            <Link to="/recommendations" className="text-xs font-medium text-slate-600 hover:text-slate-900 underline underline-offset-4">
              View in Most Useful Charts →
            </Link>
          </div>
          {results.length === 0 ? (
            <div className="bg-white rounded-2xl border border-dashed border-slate-200 p-12 text-center">
              <p className="text-sm text-slate-600">📊 No charts yet. Ask a question to get started!</p>
              <p className="text-xs text-slate-500 mt-2">Try “Show sales by region” or “Compare revenue across regions”</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4">
              {results.filter(r => r.spec.status !== 'skipped').map((r, idx) => (
                <ChartCard
                  key={r.spec.id}
                  result={r}
                  selected={activeChartId === r.spec.id}
                  onSelect={() => setActiveChart(r.spec.id)}
                />
              ))}
            </div>
          )}
          {results.filter(r => r.spec.status === 'skipped').length > 0 && (
            <details className="rounded-xl border bg-amber-50/50 p-3">
              <summary className="text-xs font-medium cursor-pointer">Skipped ({results.filter(r => r.spec.status==='skipped').length})</summary>
              <ul className="mt-2 space-y-1">
                {results.filter(r=> r.spec.status==='skipped').map(r=> (
                  <li key={r.spec.id} className="text-xs text-amber-800">• <strong>{r.spec.title}</strong> — {r.spec.skip_reason}</li>
                ))}
              </ul>
            </details>
          )}
        </div>

        {/* RIGHT COLUMN: Generation + Details 35% */}
        <div className="lg:w-[35%] space-y-4">
          <GenerationSection onChartsGenerated={handleChartsGenerated} onLoading={setLoading} onError={handleError} />
          {loading && <div className="bg-white rounded-2xl border border-slate-200 p-4 text-center text-sm text-slate-500">Generating charts...</div>}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-3">
              <p className="text-xs text-red-700">⚠️ {error}</p>
              <button onClick={() => setError(null)} className="mt-2 text-xs underline text-red-600">Dismiss</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
