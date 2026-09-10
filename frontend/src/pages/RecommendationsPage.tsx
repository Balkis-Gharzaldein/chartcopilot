import { useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { ChartCard } from '../components/ChartCard'

export function RecommendationsPage() {
  const { workbookId, results, setResults, activeChartId, setActiveChart, isGenerating, setGenerating } = useStore()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasWorkbook = !!workbookId
  // DashboardShell owns automatic generation. A second mount effect here
  // raced the shared backend plan and could execute a different request.
  async function regenerate() {
    if (!workbookId || isGenerating) return
    setLoading(true)
    setGenerating(true)
    setError(null)
    try {
      const plan = await api.plan(workbookId, ['Analyze this data'])
      const exec = await api.execute(workbookId)
      if (useStore.getState().workbookId === workbookId) setResults(exec.results, plan.specs, exec.narrative)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Recommendation generation failed.')
    } finally { setLoading(false); setGenerating(false) }
  }

  if (!hasWorkbook) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-8 text-center">
        <p className="text-sm font-medium text-slate-900">No dataset loaded</p>
        <p className="text-xs text-slate-500 mt-1">Upload a dataset to see recommended visualizations.</p>
      </div>
    )
  }

  if (loading || isGenerating) {
    return (
      <div>
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-900">Most Useful Visualizations</h2>
          <p className="text-[11px] text-slate-500">Charts selected by ChartCopilot based on your dataset</p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {[0,1,2,3,4,5].map(i=> (
            <div key={i} className="bg-white rounded-2xl border border-slate-200 p-3 space-y-3">
              <div className="h-4 w-2/3 shimmer rounded-md" />
              <div className="h-[240px] rounded-xl shimmer" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const planned = results.filter(r => r.spec.id.startsWith('exp_') && r.spec.status !== 'skipped' && r.figure_json)

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-slate-900">Most Useful Visualizations</h2>
        <p className="text-[11px] text-slate-500">Selected for data completeness, variation, and distinct analytical questions. Review aggregation assumptions in each chart.</p>
      </div>

      {planned.length ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {planned.map(r => (
            <ChartCard key={r.spec.id} result={r} selected={activeChartId===r.spec.id} onSelect={()=> setActiveChart(r.spec.id)} />
          ))}
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-dashed border-slate-200 p-8 text-center">
          <p className="text-sm text-slate-500">Generate a dataset overview to explore distributions, comparisons, and trends supported by your data.</p>
          <button onClick={regenerate} className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm text-white">Generate overview</button>
          {error && <p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}
        </div>
      )}
    </div>
  )
}
