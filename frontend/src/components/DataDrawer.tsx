import { useStore } from '../lib/store'
import { CountUp } from './ui/CountUp'
import { Skeleton } from './ui/Skeleton'

export function DataDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { profiles, filename, specs } = useStore()
  const skipped = specs.filter(s => s.status === 'skipped')
  const planned = specs.filter(s => s.status === 'planned')

  return (
    <div className={`fixed inset-0 z-40 ${open ? '' : 'pointer-events-none'}`}>
      <div className={`absolute inset-0 bg-slate-900/20 backdrop-blur-[1px] transition-opacity ${open ? 'opacity-100' : 'opacity-0'}`} onClick={onClose} />
      <div className={`absolute left-0 top-0 h-full w-[340px] max-w-[86vw] bg-white border-r border-slate-200 shadow-xl flex flex-col transition-transform ${open ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="h-12 flex items-center justify-between px-4 border-b border-slate-200 bg-white shrink-0">
          <span className="text-sm font-semibold text-slate-900">Dataset</span>
          <button onClick={onClose} className="h-8 w-8 rounded-full hover:bg-slate-100 flex items-center justify-center text-slate-600">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-5 bg-slate-50">
          {!filename ? (
            <p className="text-sm text-slate-500">No dataset loaded.</p>
          ) : profiles.length === 0 ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (
            <>
              <div className="rounded-xl border border-slate-200 bg-white p-3 fade-slide card-shadow">
                <p className="text-xs text-slate-500">File</p>
                <p className="text-sm font-medium truncate text-slate-900">{filename}</p>
                <p className="text-xs text-slate-500 mt-1">
                  {profiles.length} sheet(s) · <CountUp value={profiles.reduce((a, p) => a + p.row_count, 0)} /> rows
                </p>
              </div>

              {profiles.map(p => (
                <div key={p.sheet_name} className="space-y-2 fade-slide">
                  <h3 className="text-sm font-semibold flex items-center gap-2 text-slate-900">
                    <span className="h-2 w-2 rounded-full bg-amber-500" /> {p.sheet_name}
                    <span className="text-xs font-normal text-slate-500">
                      <CountUp value={p.row_count} /> rows · <CountUp value={p.columns.length} /> cols
                    </span>
                  </h3>
                  <div className="rounded-xl border border-slate-200 overflow-hidden bg-white card-shadow">
                    <div className="max-h-[260px] overflow-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-slate-50 sticky top-0 border-b border-slate-200">
                          <tr className="text-left text-slate-600">
                            <th className="px-3 py-2 font-medium">Column</th>
                            <th className="px-3 py-2 font-medium">Type</th>
                            <th className="px-3 py-2 font-medium text-right">Uq</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.columns.map(c => (
                            <tr key={c.name} className="border-t border-slate-100 hover:bg-slate-50/60">
                              <td className="px-3 py-1.5 font-medium truncate max-w-[140px] text-slate-900" title={c.name}>{c.name}</td>
                              <td className="px-3 py-1.5 text-slate-500">{c.dtype}</td>
                              <td className="px-3 py-1.5 text-right text-slate-600">
                                <CountUp value={c.unique_count} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <details className="text-xs bg-white rounded-xl border border-slate-200 p-3">
                    <summary className="cursor-pointer text-slate-600 hover:text-slate-900">Samples</summary>
                    <div className="mt-2 space-y-1">
                      {p.columns.slice(0,4).map(c => (
                        <div key={c.name} className="flex gap-2">
                          <span className="font-medium shrink-0 text-slate-900">{c.name}:</span>
                          <span className="text-slate-500 truncate">{c.sample_values.slice(0,3).join(', ') || '—'}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                </div>
              ))}

              {(planned.length > 0 || skipped.length > 0) && (
                <div className="space-y-2 pt-4 border-t border-slate-200">
                  <h4 className="text-xs font-semibold text-slate-900">Plan</h4>
                  {planned.length > 0 && <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-2 py-1">{planned.length} chart(s) planned</p>}
                  {skipped.length > 0 && (
                    <div className="rounded-xl bg-amber-50 border border-amber-200 p-3">
                      <p className="text-xs font-medium text-amber-800">{skipped.length} skipped</p>
                      {skipped.map(s => (
                        <p key={s.id} className="text-[11px] text-amber-700 mt-1">• {s.title}: {s.skip_reason}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
