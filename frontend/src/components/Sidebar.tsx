import { useStore } from '../lib/store'

type NavKey = 'dataset' | 'visualization' | 'recommendations'

type Props = {
  active: NavKey
  onNav: (k: NavKey) => void
  onChangeDataset: () => void
  onViewProfile: () => void
}

export function Sidebar({ active, onNav, onChangeDataset, onViewProfile }: Props) {
  const { filename, profiles } = useStore()
  const rowCount = profiles.reduce((a, p) => a + p.row_count, 0)
  const colCount = profiles.reduce((a, p) => a + p.columns.length, 0)
  const hasData = !!filename
  const isDatasetActive = active === 'dataset'

  const itemCls = (isActive: boolean) =>
    `w-full text-left px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-2.5 transition-colors ${
      isActive ? 'bg-white/10 text-white' : 'text-slate-400 hover:text-white hover:bg-white/5'
    }`

  return (
    <aside className="sidebar-bg text-slate-200 flex flex-col h-full">
      <div className="px-5 pt-6 pb-4">
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-amber-500 flex items-center justify-center text-white font-bold text-sm">◈</div>
          <span className="font-semibold tracking-tight text-white text-sm">ChartCopilot</span>
        </div>
        <p className="text-[11px] text-slate-400 mt-1">AI Data Analyst</p>
      </div>

      <nav className="px-3 mt-2 space-y-1">
        <button onClick={() => onNav('dataset')} className={itemCls(isDatasetActive)} aria-current={isDatasetActive ? 'page' : undefined}>
          <span className="text-[13px]">▭</span> Dataset
        </button>
        <button onClick={() => onNav('visualization')} className={itemCls(active === 'visualization')}>
          <span className="text-[13px]">▣</span> Visualization
        </button>
        <button onClick={() => onNav('recommendations')} className={itemCls(active === 'recommendations')}>
          <span className="text-[13px]">✦</span> Most Useful Charts
        </button>
      </nav>

      <div className="mt-6 mx-3 rounded-xl bg-white/5 border border-white/10 p-3">
        <p className="text-[11px] font-semibold text-white">Current Dataset</p>
        {hasData ? (
          <>
            <p className="text-xs text-white mt-1.5 truncate" title={filename!}>{filename}</p>
            <p className="text-[11px] text-slate-400 mt-1">
              {rowCount.toLocaleString()} rows · {colCount} columns
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button onClick={onChangeDataset} className="px-2.5 py-1.5 rounded-lg bg-white text-slate-900 text-[11px] font-medium hover:bg-slate-100">
                Change dataset
              </button>
              <button onClick={onViewProfile} className="px-2.5 py-1.5 rounded-lg bg-white/10 text-white text-[11px] font-medium border border-white/15 hover:bg-white/15">
                View profile
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-slate-400 mt-1.5">No dataset loaded</p>
            <button onClick={onChangeDataset} className="mt-3 w-full px-2.5 py-1.5 rounded-lg bg-amber-500 text-white text-[11px] font-medium hover:bg-amber-600">
              Upload dataset
            </button>
          </>
        )}
      </div>

      <div className="mt-auto px-3 py-4 text-[10px] text-slate-500">
        <p>ChartCopilot · Sandbox verified</p>
      </div>
    </aside>
  )
}
