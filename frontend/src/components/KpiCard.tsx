import { CountUp } from './ui/CountUp'

export function KpiCard({ label, value, sub, accent }: { label: string; value: number; sub?: string; accent?: string }) {
  return (
    <div className="kpi-card bg-white rounded-2xl border border-slate-200 p-4 card-shadow">
      <p className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <p className="text-xl font-semibold tracking-tight text-slate-900 mt-1">
        <CountUp value={value} />
      </p>
      {sub && <p className="text-[11px] text-slate-500 mt-1">{sub}</p>}
      {accent && <div className="mt-3 h-1 w-8 rounded-full" style={{ background: accent }} />}
    </div>
  )
}
