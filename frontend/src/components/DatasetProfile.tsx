import { useState } from 'react'
import { useStore } from '../lib/store'
import { CountUp } from './ui/CountUp'
import { ROLE_COLORS, SIGNAL, signalForNull, signalForSkew, signalForOutlier } from '../lib/chartTheme'

function roleOf(col: any, dpCol?: any): string {
  if (dpCol?.role) return dpCol.role.charAt(0).toUpperCase() + dpCol.role.slice(1)
  const n = col.name.toLowerCase()
  const temporalTokens = ["date","time","year","month","day","quarter","week","period","timestamp","created","updated","posted","deadline","found"]
  if (temporalTokens.some(t=> n.includes(t)) || col.dtype.toLowerCase().includes('date') || col.dtype.toLowerCase().includes('datetime')) return 'Temporal'
  if (['int64','int32','float64','float32','Int64','Float64'].includes(col.dtype)) {
    if (['id','identifier','key','uuid','url'].includes(n) || n.endsWith('_id')) return 'Identifier'
    return 'Numeric'
  }
  if (['id','identifier','key','uuid','url'].includes(n) || n.endsWith('_id')) return 'Identifier'
  return 'Categorical'
}

export function DatasetProfile() {
  const { profiles, dataProfiles } = useStore() as any
  const [open, setOpen] = useState(false)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [showMatrix, setShowMatrix] = useState(false)

  if (!profiles.length) return null
  const sheet = profiles.find((p:any)=> p.columns.length) || profiles[0]
  const dp = dataProfiles ? (dataProfiles[sheet.sheet_name] || Object.values(dataProfiles)[0] as any) : null
  const dpByName = (name: string) => dp?.columns?.find((c:any)=> c.name===name)

  return (
    <div className="bg-white rounded-2xl border border-slate-200 card-shadow overflow-hidden">
      <button onClick={()=> setOpen(v=>!v)} className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50">
        <div className="text-left">
          <p className="text-sm font-semibold text-slate-900">Dataset Profile</p>
          <p className="text-[11px] text-slate-500">{sheet.sheet_name} · {sheet.columns.length} columns · <CountUp value={sheet.row_count}/> rows{dp ? ` · ${dp.duplicate_rows} dup` : ''}{dp?.constant_columns?.length ? ` · ${dp.constant_columns.length} constant` : ''}</p>
        </div>
        <span className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`}>⌄</span>
      </button>
      {open && (
        <div className="px-4 pb-3 fade-slide space-y-3">
          <div className="rounded-xl border border-slate-200 overflow-hidden">
            <div className="max-h-[420px] overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur border-b shadow-[0_1px_0_0_rgba(0,0,0,0.06)]">
                  <tr className="text-left text-slate-500">
                    <th className="px-3 py-2 font-medium">Column</th>
                    <th className="px-3 py-2 font-medium">Role</th>
                    <th className="px-3 py-2 font-medium">Type</th>
                    <th className="px-3 py-2 font-medium text-right">Unique</th>
                    <th className="px-3 py-2 font-medium text-right">Null %</th>
                    <th className="px-3 py-2 font-medium text-right">Memory</th>
                  </tr>
                </thead>
                <tbody>
                  {sheet.columns.map((c:any)=> {
                    const dpCol = dpByName(c.name)
                    const role = roleOf(c, dpCol)
                    const roleKey = role.toLowerCase() as keyof typeof ROLE_COLORS
                    const rc = ROLE_COLORS[roleKey] || ROLE_COLORS.categorical
                    const badge = `${rc.bg} ${rc.text} ${rc.border}`
                    const nullPctNum = dpCol?.null_pct ?? (c.null_count && sheet.row_count ? (c.null_count/sheet.row_count)*100 : 0)
                    const nullPct = typeof nullPctNum === 'number' ? nullPctNum : parseFloat(String(nullPctNum))
                    const nullSig = signalForNull(nullPct)
                    const nullHigh = nullSig.label === 'critical'
                    const isConstant = dpCol?.is_constant
                    const isNear = dpCol?.is_near_constant
                    const skewSig = signalForSkew(dpCol?.skewness ?? null)
                    const outSig = signalForOutlier(dpCol?.outlier_pct ?? null)
                    // row left border signal: worst of null/skew/outlier/constant
                    const rowSignal = isConstant ? SIGNAL.critical : isNear ? SIGNAL.warning : nullHigh ? SIGNAL.critical : outSig.label==='critical' ? SIGNAL.critical : skewSig.label==='critical' ? SIGNAL.critical : outSig.label==='warning' || skewSig.label==='warning' ? SIGNAL.warning : nullHigh ? SIGNAL.warning : SIGNAL.good
                    const isExpanded = expandedRow === c.name
                    const memText = dpCol?.memory_mb != null ? `${dpCol.memory_mb} MB · ${dpCol.memory_kb} KB` : dpCol?.memory_kb != null ? `${dpCol.memory_kb} KB` : '—'
                    return (
                      <>
                        <tr key={c.name} onClick={()=> setExpandedRow(isExpanded ? null : c.name)} className={`border-t hover:bg-slate-50/60 cursor-pointer ${isExpanded ? 'bg-slate-50' : ''} border-l-2 ${rowSignal.label==='critical' ? 'border-l-red-300' : rowSignal.label==='warning' ? 'border-l-amber-300' : rowSignal.label==='good' ? 'border-l-emerald-300' : 'border-l-transparent'}`}>
                          <td className="px-3 py-2 font-medium text-slate-900 max-w-[160px] truncate" title={c.name}>
                            <span className="flex items-center gap-1.5">
                              {c.name}
                              {isConstant && <span className={`px-1.5 py-0.5 rounded border text-[9px] ${SIGNAL.critical.bg} ${SIGNAL.critical.text} ${SIGNAL.critical.border}`}>CONSTANT</span>}
                              {!isConstant && isNear && <span className={`px-1.5 py-0.5 rounded border text-[9px] ${SIGNAL.warning.bg} ${SIGNAL.warning.text} ${SIGNAL.warning.border}`}>NEAR-CONST</span>}
                              {dpCol?.outlier_count > 0 && <span className={`h-1.5 w-1.5 rounded-full ${outSig.dot}`} title={`${dpCol.outlier_count} outliers (${dpCol.outlier_pct}%)`} />}
                              {nullSig.label !== 'good' && nullSig.label !== 'neutral' && <span className={`h-1.5 w-1.5 rounded-full ${nullSig.dot}`} title={`Missing ${nullPct.toFixed(1)}%`} />}
                            </span>
                          </td>
                          <td className="px-3 py-2"><span className={`px-2 py-0.5 rounded-full border text-[10px] font-medium ${badge}`}>{role}</span></td>
                          <td className="px-3 py-2 text-slate-500">{c.dtype}</td>
                          <td className="px-3 py-2 text-right text-slate-600"><CountUp value={c.unique_count}/></td>
                          <td className={`px-3 py-2 text-right ${nullSig.text} ${nullSig.label==='critical' ? 'font-medium' : ''}`}>{`${nullPct.toFixed(1)}%`}<span className="ml-1 text-[10px] text-slate-400">{isExpanded ? '▾' : '▸'}</span></td>
                          <td className="px-3 py-2 text-right text-slate-500 text-[11px]">{memText}</td>
                        </tr>
                        {isExpanded && dpCol && (
                          <tr key={`${c.name}-drawer`} className="border-t bg-slate-50/50">
                            <td colSpan={6} className="px-3 py-3">
                              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px]">
                                <div className={`bg-white rounded-lg border p-2.5 ${nullSig.border} ${nullSig.bg}`}>
                                  <p className={`font-semibold ${nullSig.text}`}>Quality · Missing per column</p>
                                  <p className="mt-1 text-slate-600">Null: <span className={`${nullSig.text} ${nullSig.label==='critical' ? 'font-medium' : ''}`}>{dpCol.null_count} ({dpCol.null_pct}%)</span> <span className={`px-1.5 py-0.5 rounded text-[10px] border ${nullSig.bg} ${nullSig.text} ${nullSig.border}`}>{nullSig.label === 'critical' ? 'critical → fix' : nullSig.label === 'warning' ? 'warning → check' : nullSig.label === 'good' ? 'good' : 'ok'}</span></p>
                                  <p className="text-slate-500">{dpCol.null_pct > 20 ? 'High missing → consider imputation/drop' : dpCol.null_pct > 5 ? 'Moderate missing → check' : 'Clean'}</p>
                                  {dpCol.is_constant ? <p className={`${SIGNAL.critical.text}`}>Constant · only ‘{dpCol.sample_values?.[0] || ''}’ → drop</p> : dpCol.is_near_constant ? <p className={`${SIGNAL.warning.text}`}>Near-constant · 95% same → low signal</p> : <p className="text-slate-500">Unique: {dpCol.cardinality} ({dpCol.cardinality_ratio ? (dpCol.cardinality_ratio*100).toFixed(1)+'%' : ''})</p>}
                                  {dp && dp.duplicate_ids && dp.duplicate_ids[c.name] ? <p className={`${SIGNAL.warning.text}`}>Duplicate IDs: {dp.duplicate_ids[c.name]}</p> : null}
                                  <p className="text-slate-500">Memory: {dpCol.memory_mb != null ? `${dpCol.memory_mb} MB · ${dpCol.memory_kb} KB` : dpCol.memory_kb != null ? `${dpCol.memory_kb} KB` : '—'} {dpCol.memory_bytes ? `(${dpCol.memory_bytes.toLocaleString()} B)` : ''}</p>
                                </div>
                                {(dpCol.mean != null || dpCol.median != null) ? (
                                  <div className={`bg-white rounded-lg border p-2.5 ${skewSig.border} ${skewSig.bg}`}>
                                    <p className={`font-semibold ${skewSig.text}`}>Distribution {skewSig.label !== 'neutral' && skewSig.label !== 'good' ? `· ${skewSig.label}` : ''}</p>
                                    <p className="text-slate-600">Mean {dpCol.mean?.toFixed(2)} · Median {dpCol.median?.toFixed(2)} · Std {dpCol.std?.toFixed(2)}</p>
                                    <p className="text-slate-600">Min {dpCol.min_val} → Max {dpCol.max_val} {dpCol.has_negatives ? '· has negatives' : ''}</p>
                                    {dpCol.skewness != null && (
                                      <p className={`${skewSig.text} ${skewSig.label==='critical' || skewSig.label==='warning' ? 'font-medium' : ''}`}>Skew {dpCol.skewness} {Math.abs(dpCol.skewness) > 2 ? '· highly skewed → consider log' : Math.abs(dpCol.skewness) > 1 ? (dpCol.skewness > 0 ? '· right-skewed → check' : '· left-skewed → check') : '· ~symmetric (good)'}</p>
                                    )}
                                    {dpCol.outlier_count != null && (
                                      <p className={`${outSig.text} ${outSig.label==='critical' ? 'font-medium' : ''}`}>Outliers {dpCol.outlier_count} ({dpCol.outlier_pct}% via {dpCol.outlier_method}) <span className={`ml-1 px-1.5 py-0.5 rounded text-[10px] border ${outSig.bg} ${outSig.text} ${outSig.border}`}>{outSig.label === 'critical' ? 'critical → fix' : outSig.label === 'warning' ? 'warning → check' : 'good'}</span></p>
                                    )}
                                  </div>
                                ) : dpCol.top_values && dpCol.top_values.length ? (
                                  <div className={`bg-white rounded-lg border p-2.5 ${SIGNAL.neutral.border}`}>
                                    <p className="font-semibold text-slate-700">Categorical summary · Top 3</p>
                                    <div className="mt-1 space-y-1.5">
                                      {dpCol.top_values.slice(0,3).map((tv:any)=> (
                                        <div key={tv.value} className="flex items-center gap-2">
                                          <span className="text-slate-700 truncate max-w-[90px] font-medium" title={tv.value}>{tv.value}</span>
                                          <div className="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden">
                                            <div className="h-1.5 rounded-full bg-amber-500" style={{ width: `${Math.min(100, tv.pct)}%` }} />
                                          </div>
                                          <span className="text-slate-600 text-[11px] whitespace-nowrap">{tv.count} · {tv.pct}%</span>
                                        </div>
                                      ))}
                                    </div>
                                    {dpCol.is_multi_valued && <p className={`${SIGNAL.warning.text} mt-1`}>Multi-valued (comma) → split</p>}
                                  </div>
                                ) : (
                                  <div className="bg-white rounded-lg border border-slate-200 p-2.5">
                                    <p className="font-semibold text-slate-700">Samples</p>
                                    <p className="text-slate-600 truncate">{(dpCol.sample_values || c.sample_values || []).slice(0,3).join(', ') || '—'}</p>
                                    {dpCol.is_multi_valued && <p className={`${SIGNAL.warning.text}`}>Multi-valued (comma) → split</p>}
                                    {dpCol.avg_label_len ? <p className="text-slate-500">Avg label len {dpCol.avg_label_len.toFixed(1)}</p> : null}
                                  </div>
                                )}
                                {dpCol.role === 'temporal' ? (
                                  <div className={`bg-white rounded-lg border p-2.5 ${dpCol.temporal_gaps ? SIGNAL.warning.border : SIGNAL.good.border} ${dpCol.temporal_gaps ? SIGNAL.warning.bg : SIGNAL.good.bg}`}>
                                    <p className={`font-semibold ${dpCol.temporal_gaps ? SIGNAL.warning.text : SIGNAL.good.text}`}>Temporal coverage {dpCol.temporal_gaps ? '· warning' : '· good'}</p>
                                    {dpCol.temporal_min ? (
                                      <>
                                        <p className="text-slate-600">{dpCol.temporal_min.slice(0,10)} → {dpCol.temporal_max ? dpCol.temporal_max.slice(0,10) : ''}</p>
                                        <p className="text-slate-600">{dpCol.temporal_coverage_days != null ? `${dpCol.temporal_coverage_days} days` : ''} {dpCol.temporal_gaps ? `· ${dpCol.temporal_gaps} gap${dpCol.temporal_gaps>1?'s':''} · check missing periods` : '· no gaps (good)'}</p>
                                        {dpCol.temporal_missing_ranges && dpCol.temporal_missing_ranges.length > 0 && (
                                          <div className="mt-1">
                                            <p className="text-slate-500">Missing ranges:</p>
                                            {dpCol.temporal_missing_ranges.slice(0,2).map((r:any,i:number)=> <p key={i} className="text-slate-600 truncate">· {r[0]} → {r[1]}</p>)}
                                            {dpCol.temporal_missing_ranges.length > 2 && <p className="text-slate-400">+{dpCol.temporal_missing_ranges.length - 2} more</p>}
                                          </div>
                                        )}
                                      </>
                                    ) : <p className="text-slate-500">No coverage computed</p>}
                                  </div>
                                ) : dpCol.role === 'categorical' && dpCol.top_values && dpCol.top_values.length ? (
                                  <div className="bg-white rounded-lg border border-slate-200 p-2.5">
                                    <p className="font-semibold text-slate-700">Top 3 values</p>
                                    <div className="mt-1 space-y-1.5">
                                      {dpCol.top_values.slice(0,3).map((tv:any)=> (
                                        <div key={tv.value} className="flex items-center gap-2">
                                          <span className="text-slate-700 truncate max-w-[90px] font-medium" title={tv.value}>{tv.value}</span>
                                          <div className="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden">
                                            <div className="h-1.5 rounded-full" style={{ width: `${Math.min(100, tv.pct)}%`, background: ROLE_COLORS.categorical.accent }} />
                                          </div>
                                          <span className="text-slate-600 text-[11px] whitespace-nowrap">{tv.count} · {tv.pct}%</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ) : (
                                  <div className="bg-white rounded-lg border border-slate-200 p-2.5">
                                    <p className="font-semibold text-slate-700">Memory</p>
                                    <p className="text-slate-600">{dpCol.memory_mb != null ? `${dpCol.memory_mb} MB` : '—'} {dpCol.memory_kb != null ? `· ${dpCol.memory_kb} KB` : ''} {dpCol.memory_bytes ? `· ${dpCol.memory_bytes.toLocaleString()} B` : ''}</p>
                                    <p className="text-slate-500 text-[11px]">Column footprint (deep)</p>
                                  </div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {/* Dataset-level footer: duplicates + constant + correlations + matrix toggle + memory */}
          {dp && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                {dp.duplicate_rows > 0 ? <span className={`px-2.5 py-1 rounded-full text-xs border ${SIGNAL.critical.bg} ${SIGNAL.critical.text} ${SIGNAL.critical.border}`}>Duplicates: {dp.duplicate_rows} ({dp.duplicate_pct}%) · critical → fix</span> : <span className={`px-2.5 py-1 rounded-full text-xs border ${SIGNAL.good.bg} ${SIGNAL.good.text} ${SIGNAL.good.border}`}>No duplicate rows · good</span>}
                {dp.total_memory_mb != null && <span className="px-2.5 py-1 rounded-full text-xs border bg-slate-50 border-slate-200 text-slate-600">Memory: {dp.total_memory_mb} MB · {dp.total_memory_kb} KB · {dp.total_memory_bytes?.toLocaleString()} B</span>}
                {dp.constant_columns.length > 0 && <span className={`px-2.5 py-1 rounded-full text-xs border ${SIGNAL.critical.bg} ${SIGNAL.critical.text} ${SIGNAL.critical.border}`}>Constant: {dp.constant_columns.join(', ')} · fix</span>}
                {dp.near_constant_columns.length > 0 && <span className={`px-2.5 py-1 rounded-full text-xs border ${SIGNAL.warning.bg} ${SIGNAL.warning.text} ${SIGNAL.warning.border}`}>Near-constant: {dp.near_constant_columns.join(', ')} · check</span>}
                {dp.top_correlations.length > 0 && dp.top_correlations.slice(0,2).map(([a,b,v]:any)=> {
                  const sig = v != null ? (Math.abs(v)>=0.7 ? SIGNAL.good : Math.abs(v)>=0.5 ? SIGNAL.warning : SIGNAL.neutral) : SIGNAL.neutral
                  return <span key={`${a}-${b}`} className={`px-2.5 py-1 rounded-full text-xs border ${sig.bg} ${sig.text} ${sig.border}`}>{a} ↔ {b} r={v} · {sig.label}</span>
                })}
              </div>
              {dp.top_correlations.length > 0 && (
                <div>
                  <button onClick={()=> setShowMatrix(v=>!v)} className="text-xs text-slate-600 hover:text-slate-900 underline underline-offset-4">
                    {showMatrix ? 'Hide correlation matrix' : 'View correlation matrix →'} {dp.top_correlations.length ? `· top r=${dp.top_correlations[0][2]}` : ''}
                  </button>
                  {showMatrix && dp.correlation_matrix && Object.keys(dp.correlation_matrix).length > 0 && (
                    <div className="mt-2 rounded-xl border border-slate-200 overflow-auto bg-white max-h-[220px]">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur border-b shadow-[0_1px_0_0_rgba(0,0,0,0.06)]">
                          <tr>
                            <th className="px-2 py-1.5 text-left font-medium text-slate-500"></th>
                            {Object.keys(dp.correlation_matrix).map(col=> <th key={col} className="px-2 py-1.5 text-right font-medium text-slate-500 truncate max-w-[80px]" title={col}>{col}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {Object.keys(dp.correlation_matrix).map(row=> (
                            <tr key={row} className="border-t">
                              <td className="px-2 py-1 font-medium text-slate-700 truncate max-w-[80px]">{row}</td>
                              {Object.keys(dp.correlation_matrix).map(col=> {
                                const v = dp.correlation_matrix[row]?.[col]
                                const abs = v != null ? Math.abs(v) : 0
                                const cls = v == null ? 'text-slate-300' : abs >= 0.7 ? `${SIGNAL.good.text} font-medium` : abs >= 0.5 ? SIGNAL.warning.text : 'text-slate-600'
                                return <td key={col} className={`px-2 py-1 text-right ${cls}`}>{v != null ? v.toFixed(2) : '—'}</td>
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="text-[10px] text-slate-400 px-2 py-1">Estimated on sampled rows when &gt;10k (if sampled, values are approximate).</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <p className="text-[11px] text-slate-500">Click a row for per-column insight (Top 3 bars, missing %, skew/outliers with signals, memory, temporal gaps). Signals: <span className={`${SIGNAL.critical.text}`}>red → fix</span>, <span className={`${SIGNAL.warning.text}`}>amber → check</span>, <span className={`${SIGNAL.good.text}`}>green → ok</span>. Sticky header stays fixed while scrolling. Backend roles (string→categorical).</p>
        </div>
      )}
    </div>
  )
}
