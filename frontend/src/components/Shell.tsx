import { useStore } from '../lib/store'
import { DataDrawer } from './DataDrawer'
import { UploadModal } from './UploadModal'
import { EmptyState } from './EmptyState'
import { CommandBar } from './CommandBar'
import { ChartGrid } from './ChartGrid'
import { ChartDrawer } from './ChartDrawer'
import { useState } from 'react'

export function Shell() {
  const { workbookId, filename, profiles, llmAvailable, results, setDataDrawer, dataDrawerOpen, activeChartId } = useStore()
  const [uploadOpen, setUploadOpen] = useState(false)


  const hasWorkbook = !!workbookId
  const hasResults = results.length > 0

  return (
    <div className="min-h-screen bg-zinc-50 flex flex-col">
      <header className="h-12 bg-white border-b flex items-center justify-between px-3 sm:px-4 shrink-0">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDataDrawer(!dataDrawerOpen)}
            className="h-8 w-8 rounded-lg hover:bg-zinc-100 flex items-center justify-center text-zinc-600"
            aria-label="Toggle dataset panel"
          >
            ☰
          </button>
          <span className="font-semibold tracking-tight text-sm">ChartCopilot</span>
          <span className="hidden sm:inline text-xs text-zinc-400">· Visualization Workspace</span>
        </div>
        <div className="flex items-center gap-2">
          {hasWorkbook ? (
            <button
              onClick={() => setDataDrawer(true)}
              className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full border bg-white text-xs hover:bg-zinc-50"
            >
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="font-medium truncate max-w-[160px]">{filename}</span>
              <span className="text-zinc-500">{profiles.reduce((a,p)=>a+p.row_count,0)} rows</span>
            </button>
          ) : (
            <span className="text-xs text-zinc-500 hidden sm:block">No dataset</span>
          )}
          <span className={`text-[11px] px-2 py-1 rounded-full border ${llmAvailable ? 'bg-violet-50 border-violet-200 text-violet-700' : 'bg-zinc-100 border-zinc-200 text-zinc-600'}`}>
            {llmAvailable ? 'LLM · on' : 'Deterministic'}
          </span>
          <button
            onClick={() => setUploadOpen(true)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium ${hasWorkbook ? 'bg-white border hover:bg-zinc-50' : 'bg-zinc-900 text-white hover:bg-zinc-800'}`}
          >
            {hasWorkbook ? 'Change dataset' : 'Upload'}
          </button>
        </div>
      </header>

      <DataDrawer open={dataDrawerOpen} onClose={() => setDataDrawer(false)} />
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} />

      {hasWorkbook && <CommandBar />}

      <main className="flex-1 flex flex-col">
        {!hasWorkbook ? (
          <EmptyState onUpload={() => setUploadOpen(true)} />
        ) : hasResults ? (
          <ChartGrid />
        ) : (
          <div className="flex-1 flex items-center justify-center px-4 py-12">
            <div className="text-center max-w-[560px]">
              <p className="text-sm font-medium">Dataset loaded — ask for a visualization</p>
              <p className="text-xs text-zinc-500 mt-1">Try: "Show revenue over time", "Compare regions", or "What are the most useful visualizations for this dataset?"</p>
              <div className="mt-4 rounded-2xl border border-dashed bg-white p-6 text-xs text-zinc-500">
                Your charts will appear here as the primary workspace. Use the command bar above — each line is a chart intent.
              </div>
            </div>
          </div>
        )}
      </main>

      {activeChartId && <ChartDrawer />}

      <footer className="h-8 border-t bg-white flex items-center justify-center text-[11px] text-zinc-400">
        ChartCopilot · General-purpose visualization — sandbox verified · deterministic fallback
      </footer>
    </div>
  )
}
