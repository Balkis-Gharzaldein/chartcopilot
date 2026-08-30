import { useStore } from '../lib/store'

export function EmptyState({ onUpload }: { onUpload: () => void }) {
  const { workbookId } = useStore()
  if (workbookId) return null
  return (
    <div className="flex-1 flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-[640px] text-center">
        <div className="mx-auto h-12 w-12 rounded-2xl bg-zinc-900 text-white flex items-center justify-center mb-4 text-xl">◈</div>
        <h1 className="text-2xl font-semibold tracking-tight">Your data, visualized</h1>
        <p className="text-sm text-zinc-500 mt-2 max-w-[520px] mx-auto">
          Upload an Excel or CSV file and ask ChartCopilot what you want to see — in plain English. Charts become the workspace, not a form.
        </p>
        <div className="mt-6 flex justify-center">
          <button onClick={onUpload} className="px-6 py-2.5 rounded-xl bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-800">
            Upload dataset
          </button>
        </div>
        <p className="text-xs text-zinc-400 mt-3">Supports .xlsx, .xls, .csv · up to 20 MB · deterministic mode works without an API key</p>
      </div>
    </div>
  )
}
