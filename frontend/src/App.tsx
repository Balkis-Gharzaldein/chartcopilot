import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { DashboardShell } from './components/DashboardShell'
import { DatasetPage } from './pages/DatasetPage'
import { VisualizationPage } from './pages/VisualizationPage'
import { RecommendationsPage } from './pages/RecommendationsPage'
import { ToastHost } from './components/ui/Toast'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<DashboardShell />}>
          <Route path="dataset" element={<DatasetPage />} />
          <Route path="visualization" element={<VisualizationPage />} />
          <Route path="recommendations" element={<RecommendationsPage />} />
          <Route path="*" element={<Navigate to="/dataset" replace />} />
        </Route>
      </Routes>
      <ToastHost />
    </BrowserRouter>
  )
}
