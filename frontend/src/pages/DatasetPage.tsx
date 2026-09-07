import { useEffect, useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { DatasetOverview } from '../components/DatasetOverview'
import { DatasetProfile } from '../components/DatasetProfile'
import { EmptyState } from '../components/EmptyState'

export function DatasetPage() {
  const { workbookId, profiles } = useStore()
  const setUploadOpen = (useStore as any)((s:any)=> s.setUploadOpen as (v:boolean)=>void)
  const setDataProfiles = (useStore as any)((s:any)=> s.setDataProfiles as (dp:any)=>void)
  const dataProfiles = (useStore as any)((s:any)=> s.dataProfiles)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)
  const hasWorkbook = !!workbookId

  useEffect(() => {
    if (!workbookId) return
    if (dataProfiles) return
    let cancelled = false
    setProfileLoading(true)
    setProfileError(null)
    api.getDataProfile(workbookId).then((raw:any) => {
      if (!cancelled) {
        // Normalize top_correlations: filter malformed entries where [0][2] would crash DatasetOverview:55
        try {
          Object.values(raw).forEach((dp:any) => {
            if (!dp || !Array.isArray(dp.top_correlations)) dp.top_correlations = []
            else dp.top_correlations = dp.top_correlations.filter((t:any) => Array.isArray(t) && t.length===3 && typeof t[2]==='number' && Number.isFinite(t[2]))
            if (!Array.isArray(dp.correlation_matrix)) dp.correlation_matrix = dp.correlation_matrix || {}
          })
        } catch {}
        setDataProfiles(raw)
        setProfileLoading(false)
      }
    }).catch(e => {
      if (!cancelled) {
        setProfileError(e.message || 'Failed to load data profile')
        setProfileLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [workbookId])

  if (!hasWorkbook) {
    return <EmptyState onUpload={() => setUploadOpen(true)} />
  }

  return (
    <div className="space-y-6">
      <div id="dataset-overview">
        <h2 className="text-sm font-semibold text-slate-900">Dataset Overview</h2>
        <p className="text-[11px] text-slate-500">Profile computed from your uploaded file via backend {profileLoading ? '· Loading expanded stats…' : ''}</p>
      </div>
      <DatasetOverview />
      {profileError && <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{profileError}</div>}
      <div id="dataset-profile">
        <DatasetProfile />
      </div>
      {profiles.length === 0 && (
        <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-6 text-center">
          <p className="text-sm text-slate-500">Profiling…</p>
          <div className="mt-3 h-2 w-32 shimmer rounded mx-auto" />
        </div>
      )}
    </div>
  )
}