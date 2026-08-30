import { useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'

export function CommandBar() {
  const { workbookId, setResults } = useStore()
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!workbookId) return null

  async function run() {
    const raw = text.trim()
    if (!raw) return
    // General exploration interface:
    // - Each non-empty line is a visualization intent.
    // - Supports: "Show revenue over time", "Compare regions",
    //   "What are the most useful visualizations for this dataset?" (heuristic fallback will map to available columns),
    //   "Create 3 different useful views", "Focus on top 10" etc. planning layer handles generic intents.
    // - Also supports edits via refine pathway when chart drawer is used (not here).
    const lines = raw.split('\n').map(s => s.trim()).filter(Boolean)
    // If user typed a single paragraph with no line breaks, treat each sentence as a line
    const effective = lines.length === 1 && lines[0].length > 80 && lines[0].includes('.') 
      ? lines[0].split(/[.]+/).map(s=>s.trim()).filter(Boolean)
      : lines
    setLoading(true)
    setError(null)
    try {
      // Planning + execution are behind-the-scenes; UI only shows elegant loading state
      const plan = await api.plan(workbookId!, effective)
      // execute all planned specs
      const exec = await api.execute(workbookId!)
      setResults(exec.results, plan.specs, exec.narrative)
    } catch (e: any) {
      setError(e.message || 'Failed to generate')
    } finally { setLoading(false) }
  }

  const suggestions = [
    'Show revenue over time',
    'Compare regions',
    'What are the most useful visualizations for this dataset?',
    'Show the distribution of revenue',
  ]

  return (
    <div className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
      <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-3">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <textarea
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run() } }}
              placeholder='Ask ChartCopilot — e.g. "Show revenue over time" or "Compare regions by sales" (one request per line, ⇧+Enter for new line, ⌘+Enter to generate)'
              rows={text.includes('\n') ? 3 : 1}
              className="w-full min-h-[44px] max-h-[96px] resize-none rounded-xl border border-zinc-200 bg-white px-3 py-2.5 pr-10 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900 focus:border-zinc-900"
            />
            <span className="absolute right-2 top-2.5 text-[10px] tracking-wide text-zinc-400 hidden sm:block">⌘↵</span>
          </div>
          <button
            onClick={run}
            disabled={loading || !text.trim()}
            className="shrink-0 h-[44px] px-5 rounded-xl bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? 'Generating…' : 'Generate'}
          </button>
        </div>
        {error && <div className="mt-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {suggestions.map(s => (
            <button key={s} onClick={() => setText(s)} className="text-xs px-2.5 py-1 rounded-full bg-zinc-100 hover:bg-zinc-200 text-zinc-700">
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
