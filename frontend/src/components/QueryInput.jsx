import { useRef, useEffect } from 'react'

const PLACEHOLDERS = [
  'Which sellers generated the highest total revenue?',
  'Show monthly order trends for 2018',
  'What is the average review score by product category?',
  'Which customer states have the most orders?',
  'Top 10 products by total freight cost?',
]

export default function QueryInput({ value, onChange, onSubmit, isLoading, maxRows, onMaxRowsChange }) {
  const ref = useRef(null)

  useEffect(() => { ref.current?.focus() }, [])

  const handleKey = e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      onSubmit()
    }
  }

  const [ph, setPh] = useStaticPlaceholder()

  return (
    <div className="relative group">
      <div className={`rounded-xl border transition-all duration-200 ${
        isLoading
          ? 'border-accent/40 shadow-[0_0_0_1px_rgba(99,102,241,0.2)]'
          : 'border-surface-4 hover:border-surface-4 focus-within:border-accent/50 focus-within:shadow-[0_0_0_1px_rgba(99,102,241,0.15)]'
      } bg-surface-2`}>
        <textarea
          ref={ref}
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={handleKey}
          placeholder={ph}
          disabled={isLoading}
          rows={3}
          className="w-full bg-transparent px-4 pt-4 pb-2 text-slate-100 placeholder:text-slate-600 text-sm leading-relaxed resize-none outline-none"
        />
        <div className="flex items-center justify-between px-4 pb-3 pt-1 border-t border-surface-4">
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-600">Max rows</span>
            <select
              value={maxRows}
              onChange={e => onMaxRowsChange(Number(e.target.value))}
              disabled={isLoading}
              className="bg-surface-3 border border-surface-4 text-slate-300 text-xs rounded px-2 py-1 outline-none"
            >
              {[10, 25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <span className="text-xs text-slate-700">⌘ Enter to run</span>
          </div>
          <button
            onClick={() => onSubmit()}
            disabled={isLoading || !value.trim()}
            className="flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all duration-150
              bg-accent text-white hover:bg-accent-light disabled:opacity-40 disabled:cursor-not-allowed
              active:scale-95"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <span className="w-3.5 h-3.5 border border-white/40 border-t-white rounded-full animate-spin" />
                Running
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 3l14 9-14 9V3z"/></svg>
                Run Query
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

function useStaticPlaceholder() {
  const [idx] = [0]
  return [PLACEHOLDERS[idx], () => {}]
}
