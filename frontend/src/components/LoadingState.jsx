import { useEffect, useState } from 'react'

const STEPS = [
  { icon: '🔍', label: 'Building schema context…' },
  { icon: '🤖', label: 'Generating SQL with AI…' },
  { icon: '🛡️', label: 'Validating through 6 safety layers…' },
  { icon: '⚡', label: 'Executing against database…' },
]

export default function LoadingState({ step = 0 }) {
  const [dots, setDots] = useState('')
  useEffect(() => {
    const t = setInterval(() => setDots(d => d.length < 3 ? d + '.' : ''), 400)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="flex flex-col items-center justify-center py-16 animate-fade-in">
      {/* spinner */}
      <div className="relative w-14 h-14 mb-6">
        <div className="absolute inset-0 rounded-full border-2 border-surface-4" />
        <div className="absolute inset-0 rounded-full border-2 border-t-accent border-l-transparent border-r-transparent border-b-transparent animate-spin" />
        <div className="absolute inset-2 rounded-full border border-accent/20" />
      </div>

      {/* steps */}
      <div className="space-y-2 w-full max-w-xs">
        {STEPS.map((s, i) => {
          const done = i < step
          const active = i === step
          return (
            <div
              key={i}
              className={`flex items-center gap-3 px-4 py-2 rounded-lg transition-all duration-300 ${
                active ? 'bg-accent/10 border border-accent/20' :
                done  ? 'opacity-40' : 'opacity-20'
              }`}
            >
              <span className="text-base">{done ? '✓' : s.icon}</span>
              <span className={`text-sm font-medium ${active ? 'text-slate-100' : 'text-slate-400'}`}>
                {s.label}{active ? dots : ''}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
