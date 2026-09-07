import { useStore } from '../lib/store'

export function EmptyState({ onUpload }: { onUpload: () => void }) {
  const { workbookId } = useStore()
  if (workbookId) return null
  return (
    <div className="flex-1 flex items-center justify-center px-4 py-10">
      <div className="w-full max-w-[640px] text-center fade-slide">
        <div className="mx-auto h-12 w-12 rounded-2xl bg-slate-900 text-white flex items-center justify-center mb-4 text-xl border border-amber-500/20">◈</div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Your data, visualized</h1>
        <p className="text-sm text-slate-500 mt-2 max-w-[520px] mx-auto">
          Upload an Excel or CSV file and ask ChartCopilot what you want to see — in plain English. Charts become the workspace, not a form.
        </p>
        {/* Arrow / pointer toward upload zone */}
        <div className="mt-4 flex flex-col items-center gap-1 text-slate-400">
          <span className="text-xs">Start here</span>
          <span className="text-lg animate-bounce text-amber-500">↓</span>
        </div>
        <div className="mt-3 flex justify-center">
          <button onClick={onUpload} className="px-6 py-2.5 rounded-xl bg-amber-500 text-white text-sm font-medium hover:bg-amber-600 chip-lift shadow-sm">
            Upload dataset
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-3">Supports .xlsx, .xls, .csv · deterministic mode works without an API key</p>
        <div className="mt-10 grid grid-cols-1 sm:grid-cols-3 gap-3 text-left">
          <div className="rounded-xl border border-slate-200 bg-white card-shadow p-3">
            <p className="text-xs font-medium text-slate-900">1. Upload</p>
            <p className="text-[11px] text-slate-500">Drop your spreadsheet. We profile it instantly.</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white card-shadow p-3">
            <p className="text-xs font-medium text-slate-900">2. Ask</p>
            <p className="text-[11px] text-slate-500">“Compare revenue by region” — in plain English.</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white card-shadow p-3">
            <p className="text-xs font-medium text-slate-900">3. Explore</p>
            <p className="text-[11px] text-slate-500">Interact, refine, and download your dashboard.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
