import { useState } from 'react'
import { useStore } from '../lib/store'

type Props = {
  chart: any
  chartIndex: number
  onRefine: (updatedChart: any, log: string) => void
}

type ChatMsg = { type: 'user' | 'bot'; message: string; isError?: boolean }

export function RefineBotPanel({ chart, chartIndex, onRefine }: Props) {
  const { workbookId } = useStore()
  const [history, setHistory] = useState<ChatMsg[]>([])
  const [userMessage, setUserMessage] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSend = async () => {
    if (!userMessage.trim()) return
    const msg = userMessage
    setHistory(prev => [...prev, { type: 'user', message: msg }])
    setLoading(true)
    try {
      const res = await fetch('/api/charts/refine', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workbookId,
          chartIndex,
          refinementRequest: msg,
          currentChartSpec: chart.spec,
        }),
      })
      const data = await res.json()
      if (data.success && data.updatedChart) {
        setHistory(prev => [...prev, { type: 'bot', message: data.refinementLog || 'Chart updated' }])
        onRefine(data.updatedChart, data.refinementLog)
      } else {
        setHistory(prev => [...prev, { type: 'bot', message: `Error: ${data.error}`, isError: true }])
      }
    } catch (err: any) {
      setHistory(prev => [...prev, { type: 'bot', message: `Error: ${err.message}`, isError: true }])
    } finally {
      setLoading(false)
      setUserMessage('')
    }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-4">
      <h4 className="text-sm font-semibold text-slate-900 flex items-center gap-1.5">💡 Refine Chart</h4>
      <div className="mt-3 max-h-[200px] overflow-auto space-y-2 p-2 bg-slate-50 rounded-xl border border-slate-200">
        {history.length === 0 && <p className="text-[11px] text-slate-500">No refinements yet. Try a suggestion below.</p>}
        {history.map((m, idx) => (
          <div key={idx} className={`rounded-lg px-3 py-2 text-xs ${m.type === 'user' ? 'bg-sky-50 border border-sky-200 text-slate-900 ml-6' : m.isError ? 'bg-red-50 border border-red-200 text-red-700 mr-6' : 'bg-white border border-slate-200 text-slate-700 mr-6'}`}>
            <strong className="text-[11px]">{m.type === 'user' ? 'You' : 'ChartCopilot'}:</strong>
            <p className="mt-1 whitespace-pre-wrap">{m.message}</p>
          </div>
        ))}
        {loading && <div className="text-xs text-slate-500">⟳ Refining...</div>}
      </div>
      <div className="mt-3">
        <textarea
          value={userMessage}
          onChange={e => setUserMessage(e.target.value)}
          placeholder="e.g., 'Change colors to blue and orange' or 'Show months instead of dates'"
          rows={3}
          className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900"
        />
        <button onClick={handleSend} disabled={loading || !userMessage.trim()} className="mt-2 w-full py-2 rounded-xl bg-slate-900 text-white text-sm font-medium hover:bg-slate-800 disabled:opacity-40">
          {loading ? 'Refining...' : 'Send Request'}
        </button>
      </div>
      <div className="mt-3">
        <p className="text-[11px] text-slate-500">Try asking to:</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <button onClick={() => setUserMessage('Change colors to blue and orange')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Change colors</button>
          <button onClick={() => setUserMessage('Show month names instead of dates on X-axis')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Adjust axis labels</button>
          <button onClick={() => setUserMessage('Change to a line chart')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Change chart type</button>
          <button onClick={() => setUserMessage('Set Y-axis range from 0 to 1000')} className="chip-lift text-xs px-2.5 py-1 rounded-full bg-slate-100 hover:bg-white border border-slate-200 text-slate-700">Adjust range</button>
        </div>
      </div>
    </div>
  )
}
