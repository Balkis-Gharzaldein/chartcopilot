type PlotlyApi = {
  newPlot: (element: HTMLElement, data: any[], layout?: any, config?: any) => Promise<any> | any
  purge?: (element: HTMLElement) => void
  downloadImage?: (element: HTMLElement, options: any) => Promise<any> | any
  Plots?: { resize?: (element: HTMLElement) => void }
}

const DTYPE_INFO: Record<string, { size: number; read: (view: DataView, offset: number) => number }> = {
  i1: { size: 1, read: (v, o) => v.getInt8(o) },
  i2: { size: 2, read: (v, o) => v.getInt16(o, true) },
  i4: { size: 4, read: (v, o) => v.getInt32(o, true) },
  u1: { size: 1, read: (v, o) => v.getUint8(o) },
  u2: { size: 2, read: (v, o) => v.getUint16(o, true) },
  u4: { size: 4, read: (v, o) => v.getUint32(o, true) },
  f4: { size: 4, read: (v, o) => v.getFloat32(o, true) },
  f8: { size: 8, read: (v, o) => v.getFloat64(o, true) },
}

function decodeTypedArray(value: any): any {
  if (!value || typeof value !== 'object' || typeof value.dtype !== 'string' || typeof value.bdata !== 'string') return value
  const info = DTYPE_INFO[value.dtype]
  if (!info) return value
  try {
    const binary = atob(value.bdata)
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    const output: number[] = []
    for (let offset = 0; offset + info.size <= bytes.byteLength; offset += info.size) output.push(info.read(view, offset))
    return output
  } catch {
    return value
  }
}

export function normalizePlotlyFigure(value: any): any {
  const decoded = decodeTypedArray(value)
  if (decoded !== value) return decoded
  if (Array.isArray(value)) return value.map(normalizePlotlyFigure)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, normalizePlotlyFigure(child)]))
}

let loadPromise: Promise<PlotlyApi> | null = null

export function loadPlotly(): Promise<PlotlyApi> {
  const existing = (window as any).Plotly as PlotlyApi | undefined
  if (existing?.newPlot) return Promise.resolve(existing)
  if (loadPromise) return loadPromise

  loadPromise = new Promise<PlotlyApi>((resolve, reject) => {
    const existingScript = document.querySelector<HTMLScriptElement>('script[data-chartcopilot-plotly]')
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve((window as any).Plotly))
      existingScript.addEventListener('error', () => reject(new Error('Plotly failed to load')))
      return
    }

    const script = document.createElement('script')
    script.src = 'https://cdn.plot.ly/plotly-2.27.0.min.js'
    script.async = true
    script.dataset.chartcopilotPlotly = 'true'
    script.onload = () => {
      const plotly = (window as any).Plotly as PlotlyApi | undefined
      if (plotly?.newPlot) resolve(plotly)
      else reject(new Error('Plotly loaded without a usable API'))
    }
    script.onerror = () => reject(new Error('Plotly failed to load'))
    document.head.appendChild(script)
  }).catch((error) => {
    loadPromise = null
    throw error
  })

  return loadPromise
}
