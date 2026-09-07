import { useStore } from '../lib/store'
import { ChartCard } from './ChartCard'

export function RecommendedCharts() {
  const { results, activeChartId, setActiveChart } = useStore()
  const isGenerating = (useStore as any)((s:any)=> s.isGenerating as boolean)

  if (isGenerating) {
    return (
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900">Most Useful Visualizations</h2>
          <span className="text-[11px] text-slate-500">Generating…</span>
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

  const planned = results.filter(r=> r.spec.status !== 'skipped' && r.figure_json)
  const skipped = results.filter(r=> r.spec.status === 'skipped')

  if (results.length===0) return null

  return (
    <div>
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-slate-900">Most Useful Visualizations</h2>
        <p className="text-[11px] text-slate-500">Recommended insights based on your dataset</p>
      </div>

      {planned.length ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {results.filter(r=> r.spec.status !== 'skipped').map(r=> (
            <ChartCard key={r.spec.id} result={r} selected={activeChartId===r.spec.id} onSelect={()=> setActiveChart(r.spec.id)} />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed bg-white p-8 text-center text-sm text-slate-500">
          No charts could be rendered. Try rephrasing or check the dataset profile.
        </div>
      )}

      {skipped.length>0 && (
        <details className="mt-4 rounded-xl border bg-amber-50/50 p-3">
          <summary className="text-xs font-medium cursor-pointer">Skipped ({skipped.length}) — {skipped[0]?.spec.skip_reason?.slice(0,60)}</summary>
          <ul className="mt-2 space-y-1">
            {skipped.map(r=> (<li key={r.spec.id} className="text-xs text-amber-800">• <strong>{r.spec.title}</strong> — {r.spec.skip_reason}</li>))}
          </ul>
        </details>
      )}
    </div>
  )
}
