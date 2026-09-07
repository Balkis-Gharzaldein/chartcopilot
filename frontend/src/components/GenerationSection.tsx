import { useState } from 'react'
import { useStore } from '../lib/store'

type Props = {
  onChartsGenerated: (charts: any[]) => void
  onLoading: (loading: boolean) => void
  onError: (error: string) => void
}

export function GenerationSection({ onChartsGenerated, onLoading, onError }: Props) {
  const { workbookId } = useStore()
  const [userQuestion, setUserQuestion] = useState('')
  const [loading, setLoading] = useState(false)

  const handleGenerate = async () => {
    if (!userQuestion.trim()) return
    if (!workbookId) {
      onError('No workbook loaded. Upload a dataset first.')
      return
    }
    setLoading(true)
    onLoading(true)
    try {
      const res = await fetch('/api/charts/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workbookId,
          userQuestion: userQuestion.trim(),
        }),
      })
      const data = await res.json()
      if (data.success && data.charts) {
        onChartsGenerated(data.charts)
        setUserQuestion('')
      } else {
        onError(data.error || 'Failed to generate charts')
      }
    } catch (err: any) {
      onError(err.message || 'Failed to generate charts')
    } finally {
      setLoading(false)
      onLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200 card-shadow p-4">
      <h3 className="text-sm font-semibold text-slate-900">Ask a Question</h3>
      <p className="text-[11px] text-slate-500 mt-1">One question at a time — ChartCopilot picks the single best chart for your intent</p>
      <textarea
        value={userQuestion}
        onChange={(e) => setUserQuestion(e.target.value)}
        placeholder="e.g., 'Show me sales by region' or 'Compare revenue across regions'"
        rows={4}
        className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900 focus:border-slate-900"
      />
      <button
        onClick={handleGenerate}
        disabled={loading || !userQuestion.trim()}
        className="mt-3 w-full py-2.5 rounded-xl bg-slate-900 text-white text-sm font-medium hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {loading ? 'Generating...' : 'Generate Chart'}
      </button>
    </div>
  )
}
