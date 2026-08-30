import * as React from 'react'
export function Button({ className='', variant='default', size='default', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default'|'ghost'|'outline', size?: 'default'|'sm'|'icon' }) {
  const base = 'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-900 disabled:opacity-50 disabled:pointer-events-none'
  const variants: Record<string,string> = {
    default: 'bg-zinc-900 text-white hover:bg-zinc-800',
    ghost: 'hover:bg-zinc-100 text-zinc-700',
    outline: 'border border-zinc-200 bg-white hover:bg-zinc-50',
  }
  const sizes: Record<string,string> = {
    default: 'h-9 px-4 py-2',
    sm: 'h-8 px-3 text-xs',
    icon: 'h-9 w-9',
  }
  return <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} {...props} />
}
