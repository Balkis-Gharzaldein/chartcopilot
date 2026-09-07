import { useStore } from '../lib/store'
import { DatasetOverview } from '../components/DatasetOverview'
import { DatasetProfile } from '../components/DatasetProfile'
import { EmptyState } from '../components/EmptyState'

export function DashboardPage() {
  const { workbookId, profiles } = useStore()
  const setUploadOpen = (useStore as any)((s:any)=> s.setUploadOpen as (v:boolean)=>void)
  const hasWorkbook = !!workbookId

  if (!hasWorkbook) {
    return <EmptyState onUpload={() => setUploadOpen(true)} />
  }

  return (
    <div className="space-y-6">
      <div id="dataset-overview">
        <h2 className="text-sm font-semibold text-slate-900">Dataset Overview</h2>
        <p className="text-[11px] text-slate-500">Profile computed from your uploaded file via backend</p>
      </div>
      <DatasetOverview />
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
