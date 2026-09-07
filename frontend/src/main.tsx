import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

const qc = new QueryClient()

// Suppress benign Chrome extension noise: "A listener indicated an asynchronous response by returning true, but the message channel closed..."
window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
  const msg = String((e as any).reason?.message || (e as any).reason || '')
  if (msg.includes('message channel closed') || msg.includes('A listener indicated an asynchronous response')) {
    e.preventDefault()
    console.debug('[ignored extension]', msg)
  }
})
window.addEventListener('error', (e: ErrorEvent) => {
  if (String(e.message || '').includes('message channel closed') || String(e.message || '').includes('A listener indicated')) {
    e.preventDefault()
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
)
