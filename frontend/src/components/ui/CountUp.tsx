import { useEffect, useRef, useState } from 'react'

export function CountUp({ value, duration = 700 }: { value: number; duration?: number }) {
  const [display, setDisplay] = useState(0)
  const raf = useRef<number | null>(null)
  const from = useRef(0)

  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setDisplay(value)
      return
    }
    const start = performance.now()
    const startVal = from.current
    const delta = value - startVal
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setDisplay(Math.round(startVal + delta * eased))
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else from.current = value
    }
    raf.current = requestAnimationFrame(tick)
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current)
    }
  }, [value, duration])

  // also update from on value change immediate if reduced motion
  useEffect(() => {
    from.current = display
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <span>{display.toLocaleString()}</span>
}
