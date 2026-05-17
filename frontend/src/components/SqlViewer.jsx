import { useState } from 'react'
import { SqlHighlight } from '../utils/sqlHighlight'

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button onClick={copy} className="text-xs text-slate-500 hover:text-slate-300 transition-colors px-2 py-1 rounded hover:bg-surface-3">
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

export default function SqlViewer({ sql, attempts }) {
  const [expanded, setExpanded] = useState(true)
  if (!sql) return null

  return (
    <div className="rounded-xl border border-surface-4 bg-surface-1 overflow-hidden animate-slide-up">
      {/* header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-surface-4 bg-surface-2">
        <div className="flex items-center gap-2.5">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
          <span className="text-xs font-semibold text-slate-300 tracking-wide uppercase">Generated SQL</span>
          {attempts > 1 && (
            <span className="flex items-center gap-1 text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              {attempts} attempts
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <CopyBtn text={sql} />
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-xs text-slate-600 hover:text-slate-300 px-2 py-1 rounded hover:bg-surface-3 transition-colors"
          >
            {expanded ? '↑ Collapse' : '↓ Expand'}
          </button>
        </div>
      </div>

      {/* SQL body */}
      {expanded && (
        <div className="overflow-x-auto">
          <pre className="px-5 py-4 text-sm leading-relaxed min-w-0">
            <SqlHighlight sql={sql} />
          </pre>
        </div>
      )}
    </div>
  )
}
