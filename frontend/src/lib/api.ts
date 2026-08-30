const BASE = ''

async function jfetch(url: string, opts: RequestInit = {}) {
  const r = await fetch(BASE + url, opts)
  const text = await r.text()
  let data: any
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) {
    const msg = data?.detail || data?.msg || text || r.statusText
    throw new Error(Array.isArray(msg) ? msg.map((m:any)=>m.msg||JSON.stringify(m)).join('; ') : String(msg))
  }
  return data
}

export const api = {
  health: () => jfetch('/api/health'),
  upload: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/workbooks', { method: 'POST', body: fd })
    const t = await r.text()
    const d = t ? JSON.parse(t) : null
    if (!r.ok) throw new Error(d?.detail || t || r.statusText)
    return d as { workbook_id: string; filename: string; profiles: any[]; sheet_names: string[]; llm_available: boolean }
  },
  getWorkbook: (id: string) => jfetch(`/api/workbooks/${id}`),
  guideline: (id: string, text_area: string = '') => jfetch(`/api/workbooks/${id}/guideline`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text_area }),
  }),
  plan: (id: string, lines: string[]) => jfetch(`/api/workbooks/${id}/plan`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lines }),
  }) as Promise<{ specs: any[]; llm_available: boolean }>,
  execute: (id: string, spec_ids?: string[]) => jfetch(`/api/workbooks/${id}/execute`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec_ids ? { spec_ids } : {}),
  }) as Promise<{ results: any[]; narrative: string; llm_available: boolean }>,
  refine: (id: string, message: string, target_index?: number) => jfetch(`/api/workbooks/${id}/refine`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(target_index !== undefined ? { message, target_index } : { message }),
  }) as Promise<{ results: any[]; narrative: string; reply: string; target_index: number | null }>,
}
