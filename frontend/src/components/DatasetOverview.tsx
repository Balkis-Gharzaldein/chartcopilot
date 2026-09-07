import { useStore } from '../lib/store'
import { KpiCard } from './KpiCard'
import { ROLE_COLORS, SIGNAL } from '../lib/chartTheme'

export function DatasetOverview() {
  const { profiles, isGenerating, dataProfiles } = useStore() as any

  if (isGenerating && profiles.length === 0) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {[0,1,2,3,4].map(i => (
          <div key={i} className="bg-white rounded-2xl border border-slate-200 p-4">
            <div className="h-3 w-12 shimmer rounded" />
            <div className="h-6 w-16 shimmer rounded mt-2" />
            <div className="h-3 w-20 shimmer rounded mt-2" />
          </div>
        ))}
      </div>
    )
  }

  if (!profiles.length) return null

  const sheet = profiles.find((p:any) => p.columns.length) || profiles[0]
  // Prefer expanded DataProfile roles if available
  const dpSheet = dataProfiles ? (dataProfiles[sheet.sheet_name] || Object.values(dataProfiles)[0] as any) : null
  const numeric = dpSheet ? dpSheet.numeric_cols.length : sheet.columns.filter((c:any) => ['int64','int32','float64','float32','Int64','Float64'].includes(c.dtype)).length
  const temporal = dpSheet ? dpSheet.temporal_cols.length : (() => {
    const temporalTokens = ["date","time","year","month","day","quarter","week","period","timestamp","created","updated","posted","deadline","found"]
    const isTemporal = (c:any) => temporalTokens.some(t=> c.name.toLowerCase().includes(t)) || c.dtype.toLowerCase().includes('date') || c.dtype.toLowerCase().includes('datetime')
    return sheet.columns.filter(isTemporal).length
  })()
  const categorical = dpSheet ? dpSheet.categorical_cols.length : Math.max(0, sheet.columns.length - numeric - temporal)
  const avgNull = dpSheet ? (dpSheet.columns.reduce((a:any,c:any)=> a + (c.null_pct||0),0) / (dpSheet.columns.length||1)).toFixed(1) : null
  const dupPct = dpSheet ? dpSheet.duplicate_pct : null
  const dupRows = dpSheet ? dpSheet.duplicate_rows : null
  const constCount = dpSheet ? (dpSheet.constant_columns.length + dpSheet.near_constant_columns.length) : null
  const top = dpSheet?.top_correlations?.[0] as [string,string,number] | undefined
  const topVal = Array.isArray(top) && typeof top[2] === 'number' && Number.isFinite(top[2]) ? top[2] : undefined

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <KpiCard label="Rows" value={sheet.row_count} sub={`${profiles.length} sheet(s)`} accent="#64748B" />
        <KpiCard label="Columns" value={sheet.columns.length} accent="#64748B" />
        <KpiCard label="Memory" value={dpSheet ? dpSheet.total_memory_mb : 0} sub={dpSheet ? `${dpSheet.total_memory_mb} MB · ${dpSheet.total_memory_kb} KB` : 'memory footprint'} accent="#64748B" />
        <KpiCard label="Numeric" value={numeric} accent={ROLE_COLORS.numeric.accent} />
        <KpiCard label="Categorical" value={Math.max(0,categorical)} accent={ROLE_COLORS.categorical.accent} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <KpiCard label="Temporal" value={temporal} accent={ROLE_COLORS.temporal.accent} />
        {dpSheet ? (
          <>
            <KpiCard label="Missing" value={avgNull != null ? parseFloat(avgNull as string) : 0} sub={`${avgNull ?? '—'}% avg null · per column`} accent={avgNull != null && parseFloat(avgNull as string) > 20 ? SIGNAL.critical.dot.replace('bg-','').replace('-500','#EF4444') as any : avgNull != null && parseFloat(avgNull as string) > 5 ? '#F59E0B' : '#10B981'} />
            <KpiCard label="Duplicates" value={dupRows != null ? dupRows : 0} sub={dupPct != null ? `${dupPct}% rows` : 'duplicate rows'} accent={dupPct != null && dupPct > 5 ? '#EF4444' : dupPct != null && dupPct > 0 ? '#F59E0B' : '#10B981'} />
            <KpiCard label="Constant" value={constCount != null ? constCount : 0} sub={`${dpSheet.constant_columns.length} const · ${dpSheet.near_constant_columns.length} near-const`} accent={constCount != null && constCount > 0 ? '#EF4444' : '#10B981'} />
            <KpiCard label="Correlated" value={topVal != null ? topVal as any : 0} sub={top ? `${top[0]}↔${top[1]}` : 'no strong pairs'} accent={topVal != null && Math.abs(topVal) >= 0.7 ? '#8B5CF6' : topVal != null && Math.abs(topVal) >= 0.5 ? '#F59E0B' : '#94A3B8'} />
          </>
        ) : (
          <>
            <KpiCard label="Missing" value={0} sub="avg null per column" accent="#EAB308" />
            <KpiCard label="Duplicates" value={0} sub="duplicate rows" accent="#EF4444" />
            <KpiCard label="Constant" value={0} sub="const · near-const" accent="#94A3B8" />
            <KpiCard label="Correlated" value={0} sub="no strong pairs" accent="#8B5CF6" />
          </>
        )}
        <KpiCard label="Coverage" value={dpSheet ? (() => {
            const tcol = dpSheet.columns.find((c:any)=> c.temporal_min)
            if (!tcol) return 0 as any
            const days = tcol.temporal_coverage_days
            return days != null ? days as any : 0 as any
          })() : 0} sub={dpSheet ? (() => {
            const tcol = dpSheet.columns.find((c:any)=> c.temporal_min)
            if (!tcol || !tcol.temporal_min) return 'no temporal'
            const min = tcol.temporal_min.slice(0,10)
            const max = tcol.temporal_max ? tcol.temporal_max.slice(0,10) : ''
            const gaps = tcol.temporal_gaps
            const days = tcol.temporal_coverage_days
            const ranges = tcol.temporal_missing_ranges
            let gapsText = gaps ? ` · ${gaps} gap${gaps>1?'s':''}` : ' · no gaps'
            if (ranges && ranges.length) gapsText += ` · missing: ${ranges.slice(0,2).map((r:any)=> `${r[0]}→${r[1]}`).join(', ')}${ranges.length>2 ? ` +${ranges.length-2} more` : ''}`
            return `${days != null ? days+'d · ' : ''}${min} → ${max}${gapsText}`
          })() : 'temporal coverage'} accent={ROLE_COLORS.temporal.accent} />
      </div>
      {/* Signal legend: good → green ok, warning → amber check, critical → red fix */}
      {dpSheet && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          <span className={`px-2.5 py-1 rounded-full border ${SIGNAL.good.bg} ${SIGNAL.good.text} ${SIGNAL.good.border}`}>● good — ok</span>
          <span className={`px-2.5 py-1 rounded-full border ${SIGNAL.warning.bg} ${SIGNAL.warning.text} ${SIGNAL.warning.border}`}>● warning — check</span>
          <span className={`px-2.5 py-1 rounded-full border ${SIGNAL.critical.bg} ${SIGNAL.critical.text} ${SIGNAL.critical.border}`}>● critical — fix</span>
          <span className="text-slate-400">· left border on rows = signal</span>
        </div>
      )}
      {dpSheet && dpSheet.top_correlations && dpSheet.top_correlations.length > 0 && top && (
        <div className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 flex flex-wrap gap-2 items-center">
          <span className="text-[11px] font-semibold text-slate-700">Strongest relationships:</span>
          {dpSheet.top_correlations.slice(0,3).map(([a,b,v]:any)=> (
            <span key={`${a}-${b}`} className={`px-2.5 py-1 rounded-full text-xs font-medium border ${Math.abs(v)>=0.7 ? 'bg-violet-50 border-violet-200 text-violet-700' : Math.abs(v)>=0.5 ? 'bg-amber-50 border-amber-200 text-amber-700' : 'bg-slate-50 border-slate-200 text-slate-600'}`}>
              {a} ↔ {b} r={v}
            </span>
          ))}
          {dpSheet.top_correlations.length > 3 && <button onClick={() => document.getElementById('dataset-profile')?.scrollIntoView({behavior:'smooth'})} className="text-xs text-slate-500 hover:text-slate-700 underline underline-offset-4">View full matrix →</button>}
        </div>
      )}
      {!dpSheet && profiles.length > 0 && (
        <div className="flex gap-2">
          <div className="h-6 w-24 shimmer rounded-full" />
          <div className="h-6 w-32 shimmer rounded-full" />
          <div className="h-6 w-28 shimmer rounded-full" />
        </div>
      )}
    </div>
  )
}
