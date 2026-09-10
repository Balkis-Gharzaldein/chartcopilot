import { create } from 'zustand'

export type SheetProfile = {
  sheet_name: string
  columns: { name: string; dtype: string; sample_values: string[]; null_count: number; unique_count: number }[]
  row_count: number
}

export type ChartSpec = {
  id: string
  sheet: string
  chart_type: string
  title: string
  x: string | null
  y: string | null
  group_by?: string | null
  agg_function?: string | null
  data_notes?: string | null
  status: 'planned' | 'skipped'
  skip_reason?: string | null
  show_tail_categories?: boolean
  label_map?: Record<string, string> | null
}

export type ChartResult = {
  spec: ChartSpec
  figure_json: string | null
  computed_summary: Record<string, any>
  figure_data: Record<string, any>[]
  adaptation_note?: string | null
  verified: boolean
  verification: Record<string, any>
  validation?: Record<string, any>
  recommendations?: ChartResult[]
}

type ToastItem = { id: string; kind: 'success' | 'error' | 'info'; message: string }

export type DataProfile = {
  sheet_name: string
  row_count: number
  columns: Array<{
    name: string; dtype: string; role: string; cardinality: number; cardinality_ratio: number; ordered: boolean
    null_count: number; null_rate: number; null_pct: number; unique_count: number; sample_values: string[]
    avg_label_len: number | null; is_multi_valued: boolean; has_negatives: boolean; has_zeros: boolean; variance_zero: boolean
    is_constant: boolean; is_near_constant: boolean
    mean: number | null; median: number | null; std: number | null; min_val: number | null; max_val: number | null; skewness: number | null
    outlier_count: number | null; outlier_pct: number | null; outlier_method: string | null
    temporal_min: string | null; temporal_max: string | null; temporal_gaps: number | null; temporal_coverage_days: number | null
    temporal_missing_ranges: Array<[string,string]> | null
    memory_bytes: number | null; memory_kb: number | null; memory_mb: number | null
    top_values: Array<{value:string,count:number,pct:number}> | null
  }>
  numeric_cols: string[]; categorical_cols: string[]; temporal_cols: string[]; identifier_cols: string[]
  duplicate_rows: number; duplicate_pct: number; duplicate_ids: Record<string, number>
  constant_columns: string[]; near_constant_columns: string[]
  correlation_matrix: Record<string, Record<string, number>>
  top_correlations: Array<[string, string, number]>
  total_memory_bytes: number; total_memory_kb: number; total_memory_mb: number
}

type Store = {
  workbookId: string | null
  filename: string | null
  profiles: SheetProfile[]
  llmAvailable: boolean
  results: ChartResult[]
  specs: ChartSpec[]
  narrative: string
  activeChartId: string | null
  drawerTab: 'data' | 'computed' | 'validation' | 'refine'
  dataDrawerOpen: boolean
  uploadOpen: boolean
  isGenerating: boolean
  dataProfiles: Record<string, DataProfile> | null
  toasts: ToastItem[]
  setWorkbook: (id: string | null, filename: string | null, profiles: SheetProfile[], llmAvailable: boolean) => void
  setResults: (results: ChartResult[], specs: ChartSpec[], narrative: string) => void
  setActiveChart: (id: string | null) => void
  setDrawerTab: (t: 'data' | 'computed' | 'validation' | 'refine') => void
  setDataDrawer: (open: boolean) => void
  setUploadOpen: (open: boolean) => void
  setGenerating: (v: boolean) => void
  setDataProfiles: (dp: Record<string, DataProfile> | null) => void
  addToast: (message: string, kind?: ToastItem['kind']) => void
  removeToast: (id: string) => void
  reset: () => void
}

export const useStore = create<Store>((set) => ({
  workbookId: null,
  filename: null,
  profiles: [],
  llmAvailable: false,
  results: [],
  specs: [],
  narrative: '',
  activeChartId: null,
  drawerTab: 'data' as const,
  dataDrawerOpen: false,
  uploadOpen: false,
  isGenerating: false,
  dataProfiles: null,
  toasts: [],
  setWorkbook: (workbookId, filename, profiles, llmAvailable) => set({ workbookId, filename, profiles, llmAvailable, dataProfiles: null, results: [], specs: [], narrative: '', activeChartId: null }),
  setResults: (results, specs, narrative) => set({ results, specs, narrative }),
  setActiveChart: (activeChartId) => set({ activeChartId }),
  setDrawerTab: (drawerTab) => set({ drawerTab }),
  setDataDrawer: (dataDrawerOpen) => set({ dataDrawerOpen }),
  setUploadOpen: (uploadOpen) => set({ uploadOpen }),
  setGenerating: (isGenerating) => set({ isGenerating }),
  setDataProfiles: (dataProfiles) => set({ dataProfiles }),
  addToast: (message, kind = 'info') =>
    set((state) => {
      const id = Math.random().toString(36).slice(2)
      const t: ToastItem = { id, kind, message }
      // auto-dismiss ~3s
      setTimeout(() => {
        ;(useStore.getState() as any).removeToast(id)
      }, 3100)
      return { toasts: [...state.toasts, t] }
    }),
  removeToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  reset: () => set({ workbookId: null, filename: null, profiles: [], results: [], specs: [], narrative: '', activeChartId: null, drawerTab: 'data' as const, dataProfiles: null }),
}))
