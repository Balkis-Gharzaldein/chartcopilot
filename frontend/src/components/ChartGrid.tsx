import { useStore } from '../lib/store'
import { ChartCard } from './ChartCard'

export function ChartGrid() {
  const { results, activeChartId, setActiveChart } = useStore()
  const isGenerating = (useStore as any)((s: any) => s.isGenerating as boolean)

  if (isGenerating) {
    return (
      <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="bg-white rounded-2xl border border-slate-200 card-shadow p-3 space-y-3">
              <div className="h-4 w-2/3 shimmer rounded-md" />
              <div className="h-[280px] rounded-xl shimmer" />
              <div className="h-3 w-1/2 shimmer" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (results.length === 0) return null

  const skipped = results.filter(r => r.spec.status === 'skipped')

  const hasCharts = results.some(r => r.figure_json)
  return (
    <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-6 space-y-6">
      {hasCharts ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {results.map(r => (
            <ChartCard
              key={r.spec.id}
              result={r}
              selected={activeChartId === r.spec.id}
              onSelect={() => setActiveChart(r.spec.id)}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed bg-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-500">
          No charts could be rendered for this request. Try rephrasing — e.g. "Compare regions by sales" or check the Data drawer for available columns.
        </div>
      )}

      {skipped.length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-amber-50/50 p-3">
          <summary className="text-sm font-medium cursor-pointer text-slate-900">Skipped ({skipped.length})</summary>
          <ul className="mt-2 space-y-1">
            {skipped.map(r => (
              <li key={r.spec.id} className="text-xs text-amber-800">• <strong>{r.spec.title}</strong> — {r.spec.skip_reason}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

export function ChartSkeletons() {
  return (
    <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
      {[0, 1].map(i => (
        <div key={i} className="bg-white rounded-2xl border border-slate-200 card-shadow p-3 space-y-3">
          <div className="h-4 w-2/3 shimmer rounded-md" />
          <div className="h-[280px] rounded-xl shimmer" />
          <div className="h-3 w-1/2 shimmer" />
        </div>
      ))}
    </div>
  )
}
