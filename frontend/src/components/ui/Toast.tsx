import { useEffect } from 'react'
import { useStore } from '../../lib/store'

type ToastKind = 'success' | 'error' | 'info'

export type ToastItem = {
  id: string
  kind: ToastKind
  message: string
}

export function ToastHost() {
  const toasts = (useStore as any)((s: any) => s.toasts as ToastItem[] | undefined) || []
  const remove = (useStore as any)((s: any) => s.removeToast as ((id: string) => void) | undefined)

  if (!toasts.length) return null
  return (
    <div className="fixed top-3 right-3 z-[80] flex flex-col gap-2 pointer-events-none">
      {toasts.map((t: ToastItem) => (
        <div
          key={t.id}
          className={`pointer-events-auto min-w-[280px] max-w-[420px] rounded-xl border px-3.5 py-2.5 text-xs shadow-lg fade-slide flex items-start gap-2 ${
            t.kind === 'success'
              ? 'bg-white border-emerald-200 text-zinc-800'
              : t.kind === 'error'
                ? 'bg-white border-red-200 text-zinc-800'
                : 'bg-white border-zinc-200 text-zinc-700'
          }`}
        >
          <span
            className={`mt-0.5 h-2 w-2 rounded-full shrink-0 ${t.kind === 'success' ? 'bg-emerald-500' : t.kind === 'error' ? 'bg-red-500' : 'bg-zinc-400'}`}
          />
          <span className="flex-1 leading-snug">{t.message}</span>
          <button
            onClick={() => remove?.(t.id)}
            className="ml-1 h-6 w-6 rounded-full hover:bg-zinc-100 flex items-center justify-center shrink-0 text-zinc-500"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

export function useToast() {
  const add = (useStore as any)((s: any) => s.addToast as ((m: string, k?: ToastKind) => void) | undefined)
  return {
    success: (m: string) => add?.(m, 'success'),
    error: (m: string) => add?.(m, 'error'),
    info: (m: string) => add?.(m, 'info'),
  }
}
