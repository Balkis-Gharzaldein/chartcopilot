import { useStore } from '../lib/store'

export function TopHeader({ onMenu, onChangeDataset }: { onMenu: () => void; onChangeDataset: () => void }) {
  const { filename, profiles, llmAvailable } = useStore()
  const hasData = !!filename
  const rowCount = profiles.reduce((a, p) => a + p.row_count, 0)

  return (
    <header className="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-4 sm:px-6 shrink-0">
      <div className="flex items-center gap-3">
        <button onClick={onMenu} className="lg:hidden h-8 w-8 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-600">
          ☰
        </button>
        <div>
          <h1 className="text-sm font-semibold tracking-tight text-slate-900">Visualization Workspace</h1>
          <p className="text-[11px] text-slate-500 hidden sm:block">Dataset → Profile → Recommended Insights → Ask ChartCopilot</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {hasData ? (
          <span className="hidden sm:inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-900 text-white text-xs">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            <span className="truncate max-w-[160px]">{filename}</span>
            <span className="text-slate-300">· {rowCount.toLocaleString()} rows</span>
          </span>
        ) : (
          <span className="hidden sm:inline text-xs text-slate-500">No dataset</span>
        )}
        <span className={`text-[11px] px-2.5 py-1 rounded-full border font-medium ${llmAvailable ? 'bg-violet-50 border-violet-200 text-violet-700' : 'bg-amber-50 border-amber-200 text-amber-700'}`}>
          {llmAvailable ? 'LLM · on' : 'Deterministic'}
        </span>
        <button onClick={onChangeDataset} className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs font-medium hover:bg-slate-800">
          Change dataset
        </button>
      </div>
    </header>
  )
}
