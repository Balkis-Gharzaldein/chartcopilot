import { useRef, useState } from 'react'
import { useStore } from '../lib/store'
import { api } from '../lib/api'
import { useToast } from './ui/Toast'

export function UploadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const setWorkbook = useStore(s => s.setWorkbook)
  const toast = useToast()

  if (!open) return null

  async function handleFile(f: File | null) {
    if (!f) return
    setError(null)
    // No hardcoded file-size limit — accept any size, backend will handle parsing
    if (!f.name.match(/\.(xlsx|xls|csv)$/i)) {
      const msg = 'Please upload an Excel (.xlsx, .xls) or CSV file.'
      setError(msg)
      toast.error(msg)
      return
    }
    setLoading(true)
    try {
      const res = await api.upload(f)
      setWorkbook(res.workbook_id, res.filename, res.profiles, res.llm_available)
      toast.success('Dataset uploaded successfully')
      onClose()
    } catch (e: any) {
      const msg = e.message || 'Upload failed'
      setError(msg)
      toast.error(msg)
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-xl w-[520px] max-w-[92vw] p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">Upload dataset</h2>
          <button onClick={onClose} className="h-8 w-8 rounded-full hover:bg-slate-100 flex items-center justify-center text-slate-500">✕</button>
        </div>
        <div
          onDragOver={e => { e.preventDefault(); setDragOver(true)}}
          onDragLeave={() => setDragOver(false)}
          onDrop={e => { e.preventDefault(); setDragOver(false); handleFile(e.dataTransfer.files[0] || null)}}
          onClick={() => inputRef.current?.click()}
          className={`upload-scale border-2 border-dashed rounded-xl p-8 text-center cursor-pointer ${dragOver ? 'border-slate-900 bg-amber-50/50 scale-[1.015]' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50 scale-100'}`}
        >
          <div className="mx-auto h-10 w-10 rounded-full bg-slate-900 text-white flex items-center justify-center mb-3">↑</div>
          <p className="text-sm font-medium text-slate-900">Drop Excel or CSV here, or click to browse</p>
          <p className="text-xs text-slate-500 mt-1">.xlsx, .xls, .csv</p>
          <input ref={inputRef} type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={e => handleFile(e.target.files?.[0] || null)} />
        </div>
        {error && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
        {loading && <div className="mt-3 text-sm text-slate-500">Uploading and profiling…</div>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg hover:bg-slate-100 text-slate-700 border border-slate-200">Cancel</button>
          <button onClick={() => inputRef.current?.click()} className="px-4 py-2 text-sm rounded-lg bg-amber-500 text-white hover:bg-amber-600">Browse files</button>
        </div>
      </div>
    </div>
  )
}
