import { useEffect } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { Sidebar } from './Sidebar'
import { TopHeader } from './TopHeader'
import { ChartDrawer } from './ChartDrawer'
import { UploadModal } from './UploadModal'
import { useState } from 'react'

export function DashboardShell() {
  const { workbookId, profiles, results, setResults, activeChartId } = useStore()
  const uploadOpen = (useStore as any)((s:any)=> s.uploadOpen as boolean)
  const setUploadOpen = (useStore as any)((s:any)=> s.setUploadOpen as (v:boolean)=>void)
  const setGenerating = (useStore as any)((s:any)=> s.setGenerating as (v:boolean)=>void)
  const location = useLocation()
  const navigate = useNavigate()
  const [mobileNav, setMobileNav] = useState(false)

  const path = location.pathname
  const active: 'dataset' | 'visualization' | 'recommendations' =
    path.startsWith('/dataset') ? 'dataset' :
    path.startsWith('/visualization') ? 'visualization' :
    path.startsWith('/recommendations') ? 'recommendations' :
    'dataset'

  // Auto-generate Most Useful Visualizations after upload (keep dataset across pages)
  useEffect(() => {
    if (!workbookId || !profiles.length) return
    if (results.length > 0) return
    let cancelled = false
    const run = async () => {
      setGenerating?.(true)
      try {
        const plan = await api.plan(workbookId, ["Analyze this data"])
        const exec = await api.execute(workbookId)
        if (!cancelled) setResults(exec.results, plan.specs, exec.narrative)
      } catch (error) {
        if (!cancelled) useStore.getState().addToast(error instanceof Error ? error.message : 'Automatic chart generation failed. Retry from Recommendations.', 'error')
      }
      finally { if (!cancelled) setGenerating?.(false) }
    }
    run()
    return () => { cancelled = true }
  }, [workbookId])

  const handleNav = (key: 'dataset' | 'visualization' | 'recommendations') => {
    setMobileNav(false)
    if (key === 'dataset') {
      navigate('/dataset')
      // Focus profiling panel without removing charts — stay on same page, scroll to profile
      setTimeout(() => document.getElementById('dataset-profile')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100)
    } else if (key === 'visualization') navigate('/visualization')
    else if (key === 'recommendations') navigate('/recommendations')
    else navigate('/dataset')
  }

  return (
    <div className="min-h-screen flex dashboard-bg">
      <div className="hidden lg:block w-[240px] shrink-0">
        <div className="fixed inset-y-0 w-[240px]">
          <Sidebar active={active} onNav={handleNav} onChangeDataset={() => setUploadOpen(true)} onViewProfile={() => handleNav('dataset')} />
        </div>
      </div>

      {mobileNav && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setMobileNav(false)} />
          <div className="absolute left-0 top-0 h-full w-[260px] shadow-xl">
            <Sidebar active={active} onNav={(k)=> { handleNav(k); setMobileNav(false)}} onChangeDataset={() => { setUploadOpen(true); setMobileNav(false)}} onViewProfile={() => { handleNav('dataset'); setMobileNav(false)}} />
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <TopHeader onMenu={() => setMobileNav(true)} onChangeDataset={() => setUploadOpen(true)} />
        <main className="flex-1 px-4 sm:px-6 py-6 max-w-[1280px] w-full mx-auto">
          <Outlet />
        </main>
        <footer className="h-8 border-t border-slate-200 bg-white flex items-center justify-center text-[11px] text-slate-400">
          ChartCopilot · Sandbox verified · Deterministic fallback
        </footer>
      </div>

      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} />
      {activeChartId && <ChartDrawer />}
    </div>
  )
}
