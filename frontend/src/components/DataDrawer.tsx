import { useStore } from '../lib/store'

export function DataDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { profiles, filename, specs } = useStore()
  const skipped = specs.filter(s => s.status === 'skipped')
  const planned = specs.filter(s => s.status === 'planned')

  return (
    <div className={`fixed inset-0 z-40 ${open ? '' : 'pointer-events-none'}`}>
      <div className={`absolute inset-0 bg-black/20 transition-opacity ${open ? 'opacity-100' : 'opacity-0'}`} onClick={onClose} />
      <div className={`absolute left-0 top-0 h-full w-[340px] max-w-[86vw] bg-white border-r shadow-xl flex flex-col transition-transform ${open ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="h-12 flex items-center justify-between px-4 border-b shrink-0">
          <span className="text-sm font-semibold">Dataset</span>
          <button onClick={onClose} className="h-8 w-8 rounded-full hover:bg-zinc-100 flex items-center justify-center">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-5">
          {!filename ? (
            <p className="text-sm text-zinc-500">No dataset loaded.</p>
          ) : (
            <>
              <div className="rounded-xl border bg-zinc-50 p-3">
                <p className="text-xs text-zinc-500">File</p>
                <p className="text-sm font-medium truncate">{filename}</p>
                <p className="text-xs text-zinc-500 mt-1">{profiles.length} sheet(s) · {profiles.reduce((a,p)=>a+p.row_count,0)} rows</p>
              </div>

              {profiles.map(p => (
                <div key={p.sheet_name} className="space-y-2">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" /> {p.sheet_name}
                    <span className="text-xs font-normal text-zinc-500">{p.row_count} rows · {p.columns.length} cols</span>
                  </h3>
                  <div className="rounded-lg border overflow-hidden">
                    <div className="max-h-[260px] overflow-auto">
                      <table className="w-full text-xs">
                        <thead className="bg-zinc-50 sticky top-0">
                          <tr className="text-left text-zinc-500">
                            <th className="px-2 py-1.5 font-medium">Column</th>
                            <th className="px-2 py-1.5 font-medium">Type</th>
                            <th className="px-2 py-1.5 font-medium text-right">Uq</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.columns.map(c => (
                            <tr key={c.name} className="border-t">
                              <td className="px-2 py-1.5 font-medium truncate max-w-[140px]" title={c.name}>{c.name}</td>
                              <td className="px-2 py-1.5 text-zinc-500">{c.dtype}</td>
                              <td className="px-2 py-1.5 text-right text-zinc-500">{c.unique_count}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  <details className="text-xs">
                    <summary className="cursor-pointer text-zinc-500 hover:text-zinc-700">Samples</summary>
                    <div className="mt-2 space-y-1">
                      {p.columns.slice(0,4).map(c => (
                        <div key={c.name} className="flex gap-2">
                          <span className="font-medium shrink-0">{c.name}:</span>
                          <span className="text-zinc-500 truncate">{c.sample_values.slice(0,3).join(', ') || '—'}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                </div>
              ))}

              {(planned.length > 0 || skipped.length > 0) && (
                <div className="space-y-2 pt-4 border-t">
                  <h4 className="text-xs font-semibold text-zinc-600">Plan</h4>
                  {planned.length > 0 && <p className="text-xs text-emerald-600">{planned.length} chart(s) planned</p>}
                  {skipped.length > 0 && (
                    <div className="rounded-lg bg-amber-50 border border-amber-200 p-2">
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
