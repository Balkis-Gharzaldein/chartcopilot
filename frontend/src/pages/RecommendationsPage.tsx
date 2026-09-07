import { useEffect, useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { ChartCard } from '../components/ChartCard'

export function RecommendationsPage() {
  const { workbookId, profiles, results, setResults, activeChartId, setActiveChart } = useStore() as any
  const [loading, setLoading] = useState(false)
  const hasWorkbook = !!workbookId
  const hasResults = results.some((r:any) => r.figure_json)

  useEffect(() => {
    if (!workbookId || !profiles.length) return
    if (hasResults) return
    let cancelled = false
    const run = async () => {
      setLoading(true)
      try {
        const plan = await api.plan(workbookId, ["Analyze this data"])
        const exec = await api.execute(workbookId)
        if (!cancelled) setResults(exec.results, plan.specs, exec.narrative)
      } catch {}
      finally { if (!cancelled) setLoading(false) }
    }
    run()
    return () => { cancelled = true }
  }, [workbookId])

  if (!hasWorkbook) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-8 text-center">
        <p className="text-sm font-medium text-slate-900">No dataset loaded</p>
        <p className="text-xs text-slate-500 mt-1">Upload a dataset to see recommended visualizations.</p>
      </div>
    )
  }

  if (loading) {
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

  const planned = results.filter((r:any) => r.spec.status !== 'skipped' && r.figure_json)

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-slate-900">Most Useful Visualizations</h2>
        <p className="text-[11px] text-slate-500">Charts selected by ChartCopilot based on your dataset</p>
      </div>

      {planned.length ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {results.filter((r:any)=> r.spec.status !== 'skipped').map((r:any)=> (
            <ChartCard key={r.spec.id} result={r} selected={activeChartId===r.spec.id} onSelect={()=> setActiveChart(r.spec.id)} />
          ))}
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-dashed border-slate-200 p-8 text-center">
          <p className="text-sm text-slate-500">No recommendations could be generated. Try uploading a richer dataset.</p>
        </div>
      )}
    </div>
  )
}
