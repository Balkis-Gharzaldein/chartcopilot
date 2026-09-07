export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`shimmer rounded-md ${className}`} />
}

export function StatCardSkeleton() {
  return (
    <div className="rounded-xl border bg-white p-3 space-y-2">
      <div className="h-3 w-16 shimmer" />
      <div className="h-5 w-24 shimmer" />
      <div className="h-3 w-20 shimmer" />
    </div>
  )
}

export function ChartSkeleton() {
  return (
    <div className="bg-white rounded-2xl border border-zinc-200 p-3 space-y-3">
      <div className="h-4 w-2/3 shimmer" />
      <div className="h-[280px] rounded-xl shimmer" />
    </div>
  )
}

export function ChipSkeleton() {
  return <div className="h-6 w-24 rounded-full shimmer" />
}
