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

type Store = {
  workbookId: string | null
  filename: string | null
  profiles: SheetProfile[]
  llmAvailable: boolean
  results: ChartResult[]
  specs: ChartSpec[]
  narrative: string
  activeChartId: string | null
  dataDrawerOpen: boolean
  uploadOpen: boolean
  setWorkbook: (id: string | null, filename: string | null, profiles: SheetProfile[], llmAvailable: boolean) => void
  setResults: (results: ChartResult[], specs: ChartSpec[], narrative: string) => void
  setActiveChart: (id: string | null) => void
  setDataDrawer: (open: boolean) => void
  setUploadOpen: (open: boolean) => void
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
  dataDrawerOpen: false,
  uploadOpen: false,
  setWorkbook: (workbookId, filename, profiles, llmAvailable) => set({ workbookId, filename, profiles, llmAvailable }),
  setResults: (results, specs, narrative) => set({ results, specs, narrative }),
  setActiveChart: (activeChartId) => set({ activeChartId }),
  setDataDrawer: (dataDrawerOpen) => set({ dataDrawerOpen }),
  setUploadOpen: (uploadOpen) => set({ uploadOpen }),
  reset: () => set({ workbookId: null, filename: null, profiles: [], results: [], specs: [], narrative: '', activeChartId: null }),
}))
