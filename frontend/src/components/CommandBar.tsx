import { useState, useMemo } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'

function generateSuggestions(profiles: any[]): string[] {
  if (!profiles || profiles.length === 0 || profiles.every((p:any) => !p.columns || p.columns.length===0)) {
    return [
      "What are the most useful visualizations for this dataset?",
      "Explore this dataset",
    ]
  }
  const sheet = profiles.find((p:any) => p.columns && p.columns.length>0) || profiles[0]
  const cols = sheet.columns as Array<{name:string,dtype:string,sample_values:string[],null_count:number,unique_count:number}>
  const rowCount = sheet.row_count || 0
  const numericDtypes = new Set(["int64","int32","float64","float32","Int64","Float64","int16","float16","int","float"])
  const temporalTokens = ["date","time","year","month","day","quarter","week","period","timestamp","created","updated","posted","deadline","found"]
  const idTokens = ["id","ids","identifier","key","uuid","url","link","code","token"]

  const isTemporal = (c: any) => {
    const n = c.name.toLowerCase()
    if (temporalTokens.some(t=> n.includes(t))) return true
    if (c.dtype && c.dtype.toLowerCase().includes("datetime")) return true
    if (c.dtype && c.dtype.toLowerCase().includes("date")) return true
    for (const s of (c.sample_values||[]).slice(0,3)) {
      if (!s) continue
      const str = String(s).trim()
      if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(str)) return true
      if (str.includes("/") || str.includes("-")) {
        const d = Date.parse(str)
        if (!isNaN(d) && str.length >= 8) return true
      }
    }
    return false
  }
  const isIdentifier = (c: any) => {
    // Temporal columns are never identifiers (high cardinality is normal)
    if (isTemporal(c)) return false
    const n = c.name.toLowerCase().trim()
    if (idTokens.includes(n) || n.endsWith("_id") || n.endsWith(" id") || n.startsWith("id ") || n.startsWith("id_")) return true
    // Do not use cardinality alone for categorical — Country with 30/30 would be false positive
    return false
  }
  const isNumeric = (c: any) => numericDtypes.has(c.dtype)
  const isCategorical = (c: any) => !isNumeric(c) && !isTemporal(c) && !isIdentifier(c)

  const temporalCols = cols.filter(c => isTemporal(c))
  const numericCols = cols.filter(c => isNumeric(c) && !isIdentifier(c))
  const categoricalCols = cols.filter(c => isCategorical(c))

  const meaningfulCats = categoricalCols
    .filter(c => c.unique_count >=2 && c.unique_count <=20)
    .sort((a,b)=> a.unique_count - b.unique_count)
  const fallbackCats = categoricalCols
    .filter(c => c.unique_count >=2 && c.unique_count <=50)
    .sort((a,b)=> a.unique_count - b.unique_count)
  const bestCat = meaningfulCats[0] || fallbackCats[0] || categoricalCols.sort((a,b)=> a.unique_count - b.unique_count)[0]
  const bestCat2 = meaningfulCats[1] || null

  const bestNumeric = numericCols[0]
  const secondNumeric = numericCols[1]

  const suggestions: string[] = []
  const seen = new Set<string>()

  const add = (s: string) => {
    if (!s || seen.has(s.toLowerCase())) return
    if (s.length > 70) return
    seen.add(s.toLowerCase())
    suggestions.push(s)
  }

  const lc = (name: string) => name.toLowerCase()

  if (temporalCols.length > 0 && bestNumeric) {
    add(`Show ${lc(bestNumeric.name)} over time`)
  }
  if (bestCat && bestNumeric) {
    add(`Compare ${lc(bestNumeric.name)} across ${lc(bestCat.name)}`)
  }
  if (bestNumeric) {
    add(`Show the distribution of ${lc(bestNumeric.name)}`)
  }
  if (bestNumeric && secondNumeric) {
    if (lc(bestNumeric.name) !== lc(secondNumeric.name)) {
      add(`Show the relationship between ${lc(bestNumeric.name)} and ${lc(secondNumeric.name)}`)
    }
  }
  if (bestCat && bestCat2 && bestNumeric && suggestions.length < 4) {
    const comp = `Compare ${lc(bestNumeric.name)} by ${lc(bestCat.name)} and ${lc(bestCat2.name)}`
    if (comp.length <= 55) add(comp)
  }

  add("What are the most useful visualizations for this dataset?")

  if (suggestions.length > 5) {
    const exploratory = suggestions.find(s=> s.toLowerCase().includes("most useful"))!
    const others = suggestions.filter(s=> s!==exploratory).slice(0,4)
    return [...others, exploratory]
  }
  return suggestions.slice(0,5)
}

export function CommandBar() {
  const { workbookId, setResults, profiles } = useStore()
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const suggestions = useMemo(()=> generateSuggestions(profiles as any), [profiles])

  if (!workbookId) return null

  async function run() {
    const raw = text.trim()
    if (!raw) return
    const lines = raw.split('\n').map(s => s.trim()).filter(Boolean)
    const effective = lines.length === 1 && lines[0].length > 80 && lines[0].includes('.') 
      ? lines[0].split(/[.]+/).map(s=>s.trim()).filter(Boolean)
      : lines
    setLoading(true)
    setError(null)
    try {
      const plan = await api.plan(workbookId!, effective)
      const exec = await api.execute(workbookId!)
      setResults(exec.results, plan.specs, exec.narrative)
    } catch (e: any) {
      setError(e.message || 'Failed to generate')
    } finally { setLoading(false) }
  }

  return (
    <div className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
      <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-3">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <textarea
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run() } }}
              placeholder='Ask ChartCopilot — e.g. "Show revenue over time" or "Compare regions by sales" (one request per line, ⌘+Enter for new line, ⌘+Enter to generate)'
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
